"""SNMP 다중 OID 수집(snmp_get_many)·collect_for_proxy 델타 계약 테스트 — Phase 2-1."""
import asyncio

import pytest
from aiosnmp.exceptions import SnmpErrorStatus

import app.services.resource_collector as rc


class FakeVarbind:
    def __init__(self, oid, value):
        self.oid = oid
        self.value = value


class FakeSnmp:
    """aiosnmp.Snmp 대체 — 클래스 속성으로 동작을 주입한다."""
    responses = {}          # oid(lstrip'.') -> value
    fail_times = 0          # 처음 N회 get 호출은 예외
    fail_exc = RuntimeError("timeout")
    get_calls = []          # 호출된 청크 기록

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, oids):
        FakeSnmp.get_calls.append(list(oids) if isinstance(oids, list) else [oids])
        if FakeSnmp.fail_times > 0:
            FakeSnmp.fail_times -= 1
            raise FakeSnmp.fail_exc
        req = oids if isinstance(oids, list) else [oids]
        return [
            FakeVarbind("." + o.lstrip("."), FakeSnmp.responses[o.lstrip(".")])
            for o in req if o.lstrip(".") in FakeSnmp.responses
        ]


@pytest.fixture()
def fake_snmp(monkeypatch):
    FakeSnmp.responses = {}
    FakeSnmp.fail_times = 0
    FakeSnmp.fail_exc = RuntimeError("timeout")
    FakeSnmp.get_calls = []
    monkeypatch.setattr(rc, "Snmp", FakeSnmp)
    return FakeSnmp


def test_get_many_maps_values_with_dot_normalization(fake_snmp):
    fake_snmp.responses = {"1.3.6.1.1": 10, "1.3.6.1.2": 20}
    res = asyncio.run(rc.snmp_get_many("h", 161, "public", ["1.3.6.1.1", ".1.3.6.1.2", "1.3.6.1.9"]))
    assert res["1.3.6.1.1"] == 10.0
    assert res[".1.3.6.1.2"] == 20.0    # 요청 표기 그대로 키 유지
    assert res["1.3.6.1.9"] is None     # 응답에 없는 OID
    assert len(fake_snmp.get_calls) == 3  # OID별 개별 GET


def test_get_many_issues_individual_gets_for_multiple_oids(fake_snmp):
    # 일부 장비의 SNMP 에이전트가 multi-OID GET을 응답 없이 버려(타임아웃) 나는
    # 수집 실패를 막기 위해 OID마다 별도 GET을 보낸다(배치 없음).
    oids = [f"1.2.3.{i}" for i in range(20)]
    fake_snmp.responses = {o: i for i, o in enumerate(oids)}
    res = asyncio.run(rc.snmp_get_many("h", 161, "public", oids))
    assert len(fake_snmp.get_calls) == 20
    assert all(len(call) == 1 for call in fake_snmp.get_calls)
    assert all(res[o] == float(i) for i, o in enumerate(oids))


def test_get_many_retries_transient_failure(fake_snmp):
    fake_snmp.responses = {"1.1": 5}
    fake_snmp.fail_times = 1  # 첫 시도 실패 → 재시도 성공
    res = asyncio.run(rc.snmp_get_many("h", 161, "public", ["1.1"]))
    assert res["1.1"] == 5.0
    assert len(fake_snmp.get_calls) == 2


def test_get_many_timeout_falls_back_to_individual(fake_snmp, monkeypatch):
    """일부 에이전트는 모르는 OID가 섞인 multi-get을 응답 없이 버린다(타임아웃).
    이 경우에도 개별 GET 폴백으로 살릴 수 있는 지표를 회수해야 한다."""
    fake_snmp.fail_times = 99  # multi-get은 계속 실패 (타임아웃성)

    individual_calls = []

    async def fake_single(host, port, community, oid, timeout_sec=2):
        individual_calls.append(oid)
        return 7.0 if oid == "1.1" else None

    monkeypatch.setattr(rc, "snmp_get", fake_single)
    res = asyncio.run(rc.snmp_get_many("h", 161, "public", ["1.1", "1.2"]))
    assert sorted(individual_calls) == ["1.1", "1.2"]
    assert res["1.1"] == 7.0
    assert res["1.2"] is None


def test_get_many_protocol_error_falls_back_to_individual(fake_snmp, monkeypatch):
    # OID마다 개별 GET을 보내므로, 프로토콜 오류는 그 OID의 요청 1회만 소모한다.
    fake_snmp.fail_times = 1
    fake_snmp.fail_exc = SnmpErrorStatus(2, "1.1")

    individual_calls = []

    async def fake_single(host, port, community, oid, timeout_sec=2):
        individual_calls.append(oid)
        return 42.0 if oid == "1.1" else None

    monkeypatch.setattr(rc, "snmp_get", fake_single)
    res = asyncio.run(rc.snmp_get_many("h", 161, "public", ["1.1", "1.2"]))
    assert individual_calls == ["1.1"]  # v1식 실패 → 해당 OID만 개별 폴백
    assert res["1.1"] == 42.0
    assert res["1.2"] is None  # 응답에 없는 OID (정상 GET, 값 없음)


# ── collect_for_proxy: 단일 GET 결과가 기존 델타 로직에 그대로 흐르는지 ──

def _mk_proxy(pid=1):
    from app.models.proxy import Proxy
    p = Proxy(host="10.0.0.1", port=22)
    p.id = pid
    return p


OIDS = {"cpu": "1.3.1", "http": "1.3.2"}


def test_collect_for_proxy_uses_individual_gets_and_delta(fake_snmp):
    rc._GLOBAL_TRAFFIC_COUNTER_CACHE.clear()
    proxy = _mk_proxy(pid=101)

    # 1주기: cpu 즉시값, http는 카운터 프라이밍이라 0.0
    fake_snmp.responses = {"1.3.1": 37, "1.3.2": 1_000_000}
    pid, result, err = asyncio.run(rc.collect_for_proxy(proxy, OIDS, "public"))
    assert err is None
    assert result["cpu"] == 37.0
    assert result["http"] == 0.0
    assert len(fake_snmp.get_calls) == 2  # 지표 2개 → OID별 개별 GET

    # 2주기: 카운터 증가 → mbps 델타 계산됨
    # (델타는 time_diff >= 1초 조건이 있으므로 캐시 시각을 10초 전으로 조정)
    counter, ts = rc._GLOBAL_TRAFFIC_COUNTER_CACHE[(101, "http")]
    rc._GLOBAL_TRAFFIC_COUNTER_CACHE[(101, "http")] = (counter, ts - 10)
    fake_snmp.responses = {"1.3.1": 40, "1.3.2": 2_000_000}
    pid, result2, err = asyncio.run(rc.collect_for_proxy(proxy, OIDS, "public"))
    assert result2["cpu"] == 40.0
    assert result2["http"] is not None and result2["http"] > 0.0

    rc._GLOBAL_TRAFFIC_COUNTER_CACHE.clear()


def test_collect_for_proxy_snmp_down_returns_none_metrics(fake_snmp):
    rc._GLOBAL_TRAFFIC_COUNTER_CACHE.clear()
    fake_snmp.fail_times = 99
    proxy = _mk_proxy(pid=102)
    pid, result, err = asyncio.run(rc.collect_for_proxy(proxy, OIDS, "public"))
    assert err is None
    assert result["cpu"] is None
    assert result["http"] is None
    rc._GLOBAL_TRAFFIC_COUNTER_CACHE.clear()


def test_collect_interface_mbps_individual_gets(fake_snmp):
    rc._INTERFACE_COUNTER_CACHE.clear()
    proxy = _mk_proxy(pid=103)
    if_oids = {"eth0": {"in_oid": "1.4.1", "out_oid": "1.4.2"}}

    fake_snmp.responses = {"1.4.1": 1000, "1.4.2": 2000}
    r1 = asyncio.run(rc.collect_interface_mbps_from_oids(proxy, "public", if_oids))
    assert len(fake_snmp.get_calls) == 2  # in/out → OID별 개별 GET
    assert r1["eth0"]["in_mbps"] == 0.0   # 프라이밍

    for direction in ("in", "out"):
        counter, ts = rc._INTERFACE_COUNTER_CACHE[(103, "eth0", direction)]
        rc._INTERFACE_COUNTER_CACHE[(103, "eth0", direction)] = (counter, ts - 10)
    fake_snmp.responses = {"1.4.1": 2_000_000, "1.4.2": 2000}
    r2 = asyncio.run(rc.collect_interface_mbps_from_oids(proxy, "public", if_oids))
    assert r2["eth0"]["in_mbps"] > 0.0
    assert r2["eth0"]["out_mbps"] == 0.0  # 카운터 불변 → 0

    rc._INTERFACE_COUNTER_CACHE.clear()
