"""SSH 풀·수집기 파싱·live 엔드포인트 테스트 — Phase 1 회귀 방지."""
import time

import pytest

import app.services.resource_collector as rc
from app.utils.ssh import ssh_pool


# ── SSHPool 유휴 정리 ──

class FakeClient:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def get_transport(self):
        return None


@pytest.fixture(autouse=True)
def clean_pool():
    ssh_pool.connections.clear()
    yield
    ssh_pool.connections.clear()


def test_evict_idle_closes_only_stale_connections():
    stale, fresh = FakeClient(), FakeClient()
    ssh_pool.connections[("old", 22, "u")] = (stale, time.time() - ssh_pool.IDLE_TIMEOUT_SEC - 1)
    ssh_pool.connections[("new", 22, "u")] = (fresh, time.time())

    with ssh_pool.pool_lock:
        ssh_pool._evict_idle(keep_key=("keep", 22, "u"))

    assert ("old", 22, "u") not in ssh_pool.connections
    assert ("new", 22, "u") in ssh_pool.connections
    assert stale.closed and not fresh.closed


def test_evict_idle_never_closes_keep_key():
    kept = FakeClient()
    key = ("keep", 22, "u")
    ssh_pool.connections[key] = (kept, time.time() - ssh_pool.IDLE_TIMEOUT_SEC - 1)
    with ssh_pool.pool_lock:
        ssh_pool._evict_idle(keep_key=key)
    assert key in ssh_pool.connections and not kept.closed


def test_close_all_empties_pool():
    c = FakeClient()
    ssh_pool.connections[("h", 22, "u")] = (c, time.time())
    ssh_pool.close_all()
    assert len(ssh_pool.connections) == 0
    assert c.closed


# ── resource_collector SSH 파싱 (SSHPool 경유 전환 후 계약) ──

@pytest.fixture()
def fake_ssh(monkeypatch):
    calls = []

    def _fake(**kwargs):
        calls.append(kwargs)
        if "df -k" in kwargs["command"]:
            return "42.5\n"
        return "  73.2 extra\nsecond line\n"

    monkeypatch.setattr(rc, "ssh_exec", _fake)
    return calls


def test_mem_parse_first_token(fake_ssh):
    assert rc.ssh_exec_and_parse_mem("h", 22, "u", "p", "cmd", 5) == 73.2


def test_disk_parse(fake_ssh):
    assert rc.ssh_exec_and_parse_disk("h", 22, "u", "p", "/opt", 5) == 42.5


def test_collector_ssh_uses_pool_with_single_retry(fake_ssh):
    rc.ssh_exec_and_parse_mem("h", 22, "u", "p", "cmd", 5)
    rc.ssh_exec_and_parse_disk("h", 22, "u", "p", "/opt", 5)
    assert all(c["use_pool"] is True and c["max_retries"] == 1 for c in fake_ssh)


def test_collector_ssh_failure_returns_none(monkeypatch):
    def _boom(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(rc, "ssh_exec", _boom)
    assert rc.ssh_exec_and_parse_mem("h", 22, "u", "p", "cmd", 5) is None
    assert rc.ssh_exec_and_parse_disk("h", 22, "u", "p", "/opt", 5) is None


def test_mem_clamp_bounds(monkeypatch):
    monkeypatch.setattr(rc, "ssh_exec", lambda **k: "5000\n")
    assert rc.ssh_exec_and_parse_mem("h", 22, "u", "p", "cmd", 5) == 1000.0
    monkeypatch.setattr(rc, "ssh_exec", lambda **k: "-3\n")
    assert rc.ssh_exec_and_parse_mem("h", 22, "u", "p", "cmd", 5) == 0.0


# ── 카운터 델타 계산 (Phase 2-1 SNMP 리팩터링 대비 계약) ──

def test_calculate_mbps_normal_delta():
    # 1초 동안 125,000 bytes = 1 Mbps
    assert rc.calculate_mbps(250_000, 125_000, 1.0) == pytest.approx(1.0)


def test_calculate_mbps_counter32_wrap():
    prev = rc.COUNTER32_MAX - 1000
    cur = 1000
    expected_bytes = 2001  # (MAX+1 - prev) + cur
    assert rc.calculate_mbps(cur, prev, 1.0) == pytest.approx(expected_bytes * 8 / 1_000_000)


def test_calculate_mbps_counter_reset_returns_zero():
    # 이전 값이 크지 않은데 감소 → 리셋으로 간주, 0
    assert rc.calculate_mbps(100, 1_000_000, 1.0) == 0.0


def test_calculate_mbps_zero_time_diff():
    assert rc.calculate_mbps(100, 50, 0) == 0.0


# ── live 엔드포인트 (wc+tail 결합 SSH 1회) ──

@pytest.fixture()
def live_proxy(db_session):
    from app.models.proxy import Proxy

    p = Proxy(host="10.9.5.1", port=22, username="u", password="pw",
              traffic_log_path="/var/log/x.log", is_active=True)
    db_session.add(p)
    db_session.commit()
    return p.id


def test_live_endpoint_single_ssh_roundtrip(client, live_proxy, monkeypatch):
    import app.api.traffic_logs as tl

    ssh_calls = []

    def fake(host, port, username, password, cmd, timeout_sec=12):
        ssh_calls.append(cmd)
        if len(ssh_calls) == 1:
            return "100\nf1 :| f2 :| 1.2.3.4 :| x\n"
        if len(ssh_calls) == 2:
            return "105\nnew1 :| f2 :| 1.2.3.4 :| x\n"
        return "105\n"

    monkeypatch.setattr(tl, "ssh_exec", fake)
    tl._live_state.pop(live_proxy, None)

    r1 = client.get(f"/api/traffic-logs/live/{live_proxy}", params={"q": "test"}).json()
    assert r1["is_initial"] is True
    assert r1["total_count"] == 100
    assert r1["new_lines"] == 1
    assert len(ssh_calls) == 1 and ssh_calls[0].startswith("wc -l < ")

    r2 = client.get(f"/api/traffic-logs/live/{live_proxy}", params={"q": "test"}).json()
    assert r2["is_initial"] is False
    assert r2["total_count"] == 105
    assert "tail -n +101" in ssh_calls[1]

    r3 = client.get(f"/api/traffic-logs/live/{live_proxy}", params={"q": "test"}).json()
    assert r3["new_lines"] == 0 and r3["records"] == []

    tl._live_state.pop(live_proxy, None)


def test_live_endpoint_requires_query(client, live_proxy):
    assert client.get(f"/api/traffic-logs/live/{live_proxy}").status_code in (400, 422)
