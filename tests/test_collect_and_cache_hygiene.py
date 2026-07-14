"""traffic-logs/collect 비동기화·인메모리 캐시 위생 테스트 — Phase 3."""
from time import monotonic

import pytest

import app.api.traffic_logs as tl
import app.services.resource_collector as rc
from app.models.proxy import Proxy


# ── /api/traffic-logs/collect (async 전환 후 응답 계약) ──

@pytest.fixture()
def two_proxies(db_session):
    p1 = Proxy(host="10.9.6.1", port=22, username="u", password="pw",
               traffic_log_path="/var/log/a.log", is_active=True)
    p2 = Proxy(host="10.9.6.2", port=22, username="u", password="pw",
               traffic_log_path="/var/log/b.log", is_active=True)
    db_session.add_all([p1, p2])
    db_session.commit()
    return p1.id, p2.id


def test_collect_success_response_contract(client, two_proxies, monkeypatch):
    monkeypatch.setattr(tl, "_fetch_and_parse_for_proxy", lambda p, q, limit, d, db: ([], None))
    r = client.post("/api/traffic-logs/collect",
                    params={"proxy_ids": f"{two_proxies[0]},{two_proxies[1]}"})
    assert r.status_code == 200
    body = r.json()
    assert body["succeeded"] == 2
    assert body["failed"] == 0
    assert body["errors"] == {}
    assert sorted(body["proxies"]) == sorted(two_proxies)


def test_collect_partial_failure(client, two_proxies, monkeypatch):
    fail_id = two_proxies[1]

    def fake_fetch(p, q, limit, d, db):
        if p.id == fail_id:
            return [], "ssh timeout"
        return [], None

    monkeypatch.setattr(tl, "_fetch_and_parse_for_proxy", fake_fetch)
    body = client.post("/api/traffic-logs/collect",
                       params={"proxy_ids": f"{two_proxies[0]},{fail_id}"}).json()
    assert body["succeeded"] == 1
    assert body["failed"] == 1
    assert body["errors"] == {str(fail_id): "ssh timeout"}


def test_collect_no_active_proxies_404(client, db_session):
    assert client.post("/api/traffic-logs/collect", params={"proxy_ids": "99999"}).status_code == 404


def test_collect_invalid_ids_400(client):
    assert client.post("/api/traffic-logs/collect", params={"proxy_ids": "x,y"}).status_code == 400


# ── 인메모리 캐시 위생 ──

def test_cleanup_stale_caches_removes_deleted_proxy_keys():
    rc._INTERFACE_COUNTER_CACHE.clear()
    rc._GLOBAL_TRAFFIC_COUNTER_CACHE.clear()
    rc._MEM_CACHE.clear()

    rc._INTERFACE_COUNTER_CACHE[(1, "eth0", "in")] = (100, 1.0)
    rc._INTERFACE_COUNTER_CACHE[(2, "eth0", "in")] = (100, 1.0)
    rc._GLOBAL_TRAFFIC_COUNTER_CACHE[(1, "http")] = (100, 1.0)
    rc._GLOBAL_TRAFFIC_COUNTER_CACHE[(2, "http")] = (100, 1.0)
    now = monotonic()
    rc._MEM_CACHE[("h1", 22, "u", "cmd")] = (50.0, now + 100)  # 유효
    rc._MEM_CACHE[("h2", 22, "u", "cmd")] = (50.0, now - 100)  # 만료

    rc.cleanup_stale_caches(active_proxy_ids={1})

    assert list(rc._INTERFACE_COUNTER_CACHE) == [(1, "eth0", "in")]
    assert list(rc._GLOBAL_TRAFFIC_COUNTER_CACHE) == [(1, "http")]
    assert list(rc._MEM_CACHE) == [("h1", 22, "u", "cmd")]

    rc._INTERFACE_COUNTER_CACHE.clear()
    rc._GLOBAL_TRAFFIC_COUNTER_CACHE.clear()
    rc._MEM_CACHE.clear()


def test_cleanup_live_state_removes_deleted_proxy():
    tl._live_state.clear()
    tl._live_state[1] = 100
    tl._live_state[2] = 200

    tl.cleanup_live_state(active_proxy_ids={1})

    assert tl._live_state == {1: 100}
    tl._live_state.clear()
