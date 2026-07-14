"""트래픽 로그 분석 API(/api/traffic-logs/analyze) 계약 테스트.

집계 수치를 정확히 고정한다 — 로드맵 Phase 2-2(SQL GROUP BY 전환) 시
이 테스트가 변경 전후 결과 동일성을 보증한다.
"""
import pytest
from app.models.proxy import Proxy
from app.models.traffic_log import TrafficLog
from app.utils.time import now_kst


@pytest.fixture()
def seeded(db_session):
    p1 = Proxy(host="10.9.1.1", port=22)
    p2 = Proxy(host="10.9.1.2", port=22)
    db_session.add_all([p1, p2])
    db_session.commit()

    ts = now_kst()

    def row(proxy_id, ip, host, status, recv, sent, action=""):
        return TrafficLog(
            proxy_id=proxy_id, collected_at=ts, client_ip=ip, url_host=host,
            response_statuscode=status, recv_byte=recv, sent_byte=sent,
            action_names=action,
        )

    rows = [
        # client A: 3건, blockA 없음
        row(p1.id, "1.1.1.1", "a.com", 200, 100, 10),
        row(p1.id, "1.1.1.1", "a.com", 200, 100, 10),
        row(p1.id, "1.1.1.1", "b.com", 404, 100, 10),
        # client B: 6건 중 4건 block (rate 0.67 → critical block_heavy)
        row(p1.id, "2.2.2.2", "a.com", 200, 50, 5, "Block"),
        row(p1.id, "2.2.2.2", "a.com", 200, 50, 5, " block "),
        row(p1.id, "2.2.2.2", "a.com", 200, 50, 5, "BLOCK"),
        row(p1.id, "2.2.2.2", "a.com", 200, 50, 5, "block"),
        row(p1.id, "2.2.2.2", "a.com", 200, 50, 5),
        row(p2.id, "2.2.2.2", "c.com", 500, 50, 5),
        # 음수 바이트는 0으로 클램프되어야 함
        row(p2.id, "3.3.3.3", "c.com", 200, -100, -10),
        # client_ip/url_host 빈 값 행: summary total에는 포함, 클라이언트/호스트 집계 제외
        row(p2.id, "", "", 200, 7, 3),
    ]
    db_session.add_all(rows)
    db_session.commit()
    return {"p1": p1.id, "p2": p2.id}


@pytest.fixture()
def result(client, seeded):
    r = client.get("/api/traffic-logs/analyze",
                   params={"proxy_ids": f"{seeded['p1']},{seeded['p2']}"})
    assert r.status_code == 200
    return r.json()


def test_summary_counts(result):
    s = result["summary"]
    assert s["total"] == 11
    assert s["blocked"] == 4          # 대소문자·공백 무시 'block'만
    assert s["unique_clients"] == 3   # 빈 IP 제외
    assert s["unique_hosts"] == 3     # 빈 host 제외
    # 음수는 0 클램프: 100*3 + 50*6 + 0 + 7 = 607
    assert s["total_recv_bytes"] == 607
    assert s["total_sent_bytes"] == 10 * 3 + 5 * 6 + 0 + 3


def test_hosts_aggregation_and_order(result):
    hosts = result["hosts"]
    assert [h["host"] for h in hosts][:1] == ["a.com"]  # 요청 수 최다
    a = next(h for h in hosts if h["host"] == "a.com")
    assert a["requests"] == 7
    assert a["recv_bytes"] == 100 * 2 + 50 * 5
    c = next(h for h in hosts if h["host"] == "c.com")
    assert c["requests"] == 2
    assert c["recv_bytes"] == 50  # 음수 행은 0


def test_clients_aggregation(result):
    clients = {c["client_ip"]: c for c in result["clients"]}
    assert clients["2.2.2.2"]["requests"] == 6
    assert clients["1.1.1.1"]["requests"] == 3
    assert clients["3.3.3.3"]["recv_bytes"] == 0  # 음수 클램프
    assert "" not in clients


def test_statuses_distribution(result):
    statuses = {s["status"]: s["count"] for s in result["statuses"]}
    assert statuses["200"] == 9
    assert statuses["404"] == 1
    assert statuses["500"] == 1


def test_proxies_distribution_uses_host_label(result, seeded):
    proxies = {p["proxy"]: p["count"] for p in result["proxies"]}
    assert proxies["10.9.1.1"] == 8
    assert proxies["10.9.1.2"] == 3


def test_anomaly_block_heavy_detected(result):
    block = [a for a in result["anomalies"] if a["type"] == "block_heavy"]
    assert len(block) == 1
    assert block[0]["subject"] == "2.2.2.2"
    assert block[0]["severity"] == "critical"  # 4/6 = 67% >= 60%
    assert "_sv" not in block[0]  # 내부 정렬 키는 응답에서 제거


def test_empty_proxy_ids_returns_400(client):
    assert client.get("/api/traffic-logs/analyze", params={"proxy_ids": " "}).status_code == 400


def test_response_top_level_keys(result):
    assert set(result.keys()) == {"summary", "hosts", "clients", "statuses", "proxies", "anomalies"}
