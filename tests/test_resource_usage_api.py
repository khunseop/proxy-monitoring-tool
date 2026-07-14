"""자원사용률 조회 API(/api/history, /api/resource-usage/stats) 계약 테스트.

로드맵 Phase 2-3(/api/history 다운샘플링) 시 하위호환(짧은 기간 경로)의 기준이 된다.
"""
from datetime import timedelta

import pytest
from app.models.proxy import Proxy
from app.models.resource_usage import ResourceUsage
from app.utils.time import now_kst


@pytest.fixture()
def seeded(db_session):
    p1 = Proxy(host="10.9.2.1", port=22)
    p2 = Proxy(host="10.9.2.2", port=22)
    db_session.add_all([p1, p2])
    db_session.commit()

    base = now_kst() - timedelta(hours=1)
    rows = []
    for i in range(6):  # p1: 10분 간격 6개
        rows.append(ResourceUsage(
            proxy_id=p1.id, cpu=10.0 * i, mem=50.0, cc=100 + i,
            collected_at=base + timedelta(minutes=10 * i),
            created_at=base, updated_at=base,
        ))
    rows.append(ResourceUsage(
        proxy_id=p2.id, cpu=99.0, mem=88.0,
        collected_at=base, created_at=base, updated_at=base,
    ))
    db_session.add_all(rows)
    db_session.commit()
    return {"p1": p1.id, "p2": p2.id, "base": base}


def test_history_filters_by_proxy_and_orders_desc(client, seeded):
    r = client.get("/api/history", params={"proxy_id": seeded["p1"]})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 6
    assert all(row["proxy_id"] == seeded["p1"] for row in rows)
    times = [row["collected_at"] for row in rows]
    assert times == sorted(times, reverse=True)  # collected_at desc


def test_history_time_range(client, seeded):
    start = (seeded["base"] + timedelta(minutes=15)).isoformat()
    end = (seeded["base"] + timedelta(minutes=45)).isoformat()
    rows = client.get("/api/history", params={
        "proxy_id": seeded["p1"], "start_time": start, "end_time": end,
    }).json()
    # 20/30/40분 시점 3개
    assert len(rows) == 3
    assert sorted(row["cpu"] for row in rows) == [20.0, 30.0, 40.0]


def test_history_multi_proxy(client, seeded):
    rows = client.get("/api/history", params={
        "proxy_ids": f"{seeded['p1']},{seeded['p2']}",
    }).json()
    assert len(rows) == 7


def test_history_schema_fields(client, seeded):
    rows = client.get("/api/history", params={"proxy_id": seeded["p2"]}).json()
    row = rows[0]
    for key in ("id", "proxy_id", "cpu", "mem", "cc", "cs", "disk",
                "interface_mbps", "collected_at"):
        assert key in row
    assert row["cpu"] == 99.0


def test_history_invalid_time_returns_400(client, seeded):
    r = client.get("/api/history", params={"proxy_id": seeded["p1"], "start_time": "bogus"})
    assert r.status_code == 400


def test_stats_contract(client, seeded):
    s = client.get("/api/resource-usage/stats").json()
    assert s["total_count"] == 7
    assert s["retention_days"] == 90
    assert s["records_by_proxy"][str(seeded["p1"])] == 6
    assert s["records_by_proxy"][str(seeded["p2"])] == 1
    assert s["oldest_record"] is not None and s["newest_record"] is not None
