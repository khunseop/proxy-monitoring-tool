"""트래픽 로그 목록 API(/api/traffic-logs) 계약 테스트.

페이징·정렬·필터·검색 동작을 고정한다.
(로드맵 Phase 2에서 쿼리를 리팩터링해도 이 계약이 유지되어야 함)
"""
import pytest
from app.models.proxy import Proxy
from app.models.traffic_log import TrafficLog
from app.utils.time import now_kst


@pytest.fixture()
def seeded(db_session):
    p1 = Proxy(host="10.9.0.1", port=22)
    p2 = Proxy(host="10.9.0.2", port=22)
    db_session.add_all([p1, p2])
    db_session.commit()

    ts = now_kst()
    rows = []
    for i in range(10):
        rows.append(TrafficLog(
            proxy_id=p1.id, collected_at=ts,
            client_ip=f"192.168.0.{i}", url_host=f"site{i}.example.com",
            response_statuscode=200 if i < 8 else 404,
            recv_byte=i * 100, sent_byte=i * 10,
        ))
    rows.append(TrafficLog(
        proxy_id=p2.id, collected_at=ts,
        client_ip="172.16.0.1", url_host="other.example.com",
        response_statuscode=200, recv_byte=999, sent_byte=1,
    ))
    db_session.add_all(rows)
    db_session.commit()
    return {"p1": p1.id, "p2": p2.id}


def test_pagination_and_total_count(client, seeded):
    r = client.get("/api/traffic-logs",
                   params={"proxy_ids": str(seeded["p1"]), "offset": 0, "limit": 3}).json()
    assert r["total_count"] == 10
    assert r["count"] == 3
    assert len(r["records"]) == 3

    r2 = client.get("/api/traffic-logs",
                    params={"proxy_ids": str(seeded["p1"]), "offset": 9, "limit": 3}).json()
    assert r2["count"] == 1


def test_default_sort_id_desc(client, seeded):
    # 응답 스키마에 id가 없으므로, id와 같은 순서로 시딩된 recv_byte로 검증
    r = client.get("/api/traffic-logs", params={"proxy_ids": str(seeded["p1"])}).json()
    vals = [rec["recv_byte"] for rec in r["records"]]
    assert vals == sorted(vals, reverse=True)


def test_sort_asc_by_column(client, seeded):
    r = client.get("/api/traffic-logs",
                   params={"proxy_ids": str(seeded["p1"]),
                           "sort_col": "recv_byte", "sort_dir": "asc"}).json()
    vals = [rec["recv_byte"] for rec in r["records"]]
    assert vals == sorted(vals)


def test_multi_proxy_query(client, seeded):
    r = client.get("/api/traffic-logs",
                   params={"proxy_ids": f"{seeded['p1']},{seeded['p2']}"}).json()
    assert r["total_count"] == 11
    assert r["requested"] == 2


def test_numeric_column_filter(client, seeded):
    r = client.get("/api/traffic-logs",
                   params={"proxy_ids": str(seeded["p1"]),
                           "filter_col": "response_statuscode", "filter_val": "404"}).json()
    assert r["total_count"] == 2
    assert all(rec["response_statuscode"] == 404 for rec in r["records"])


def test_text_column_filter_contains(client, seeded):
    r = client.get("/api/traffic-logs",
                   params={"proxy_ids": str(seeded["p1"]),
                           "filter_col": "url_host", "filter_val": "site3"}).json()
    assert r["total_count"] == 1
    assert r["records"][0]["url_host"] == "site3.example.com"


def test_search_across_columns(client, seeded):
    # client_ip와 url_host 어느 쪽이든 매칭
    r = client.get("/api/traffic-logs",
                   params={"proxy_ids": str(seeded["p1"]), "search": "192.168.0.5"}).json()
    assert r["total_count"] == 1

    r2 = client.get("/api/traffic-logs",
                    params={"proxy_ids": str(seeded["p1"]), "search": "example.com"}).json()
    assert r2["total_count"] == 10


def test_invalid_proxy_ids_returns_400(client, seeded):
    r = client.get("/api/traffic-logs", params={"proxy_ids": "abc"})
    assert r.status_code == 400


def test_proxy_id_returned_as_string(client, seeded):
    # 스키마 계약: proxy_id는 str로 직렬화됨
    r = client.get("/api/traffic-logs", params={"proxy_ids": str(seeded["p1"]), "limit": 1}).json()
    assert isinstance(r["records"][0]["proxy_id"], str)
