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


def test_history_downsampling_buckets_and_averages(client, db_session):
    """구간이 길어 버킷 폭 >= 60초가 되면 시간 버킷 평균으로 집계된다 (Phase 2-3)."""
    from datetime import datetime

    p = Proxy(host="10.9.2.9", port=22)
    db_session.add(p)
    db_session.commit()

    # 2026-01-01 00:00(KST 벽시계)은 600초 버킷 경계에 정렬됨
    t0 = datetime(2026, 1, 1, 0, 0, 0)
    rows = [
        # 버킷 1: 10초·20초 시점 → cpu 평균 15
        ResourceUsage(proxy_id=p.id, cpu=10.0, collected_at=t0 + timedelta(seconds=10),
                      created_at=t0, updated_at=t0),
        ResourceUsage(proxy_id=p.id, cpu=20.0, interface_mbps='{"eth0": {"in_mbps": 1.5, "out_mbps": 0.5}}',
                      collected_at=t0 + timedelta(seconds=20), created_at=t0, updated_at=t0),
        # 버킷 2: 15분 시점
        ResourceUsage(proxy_id=p.id, cpu=60.0, collected_at=t0 + timedelta(seconds=900),
                      created_at=t0, updated_at=t0),
    ]
    db_session.add_all(rows)
    db_session.commit()

    # 구간 60,000초 / max_points=100 → 버킷 600초
    resp = client.get("/api/history", params={
        "proxy_id": p.id,
        "start_time": "2026-01-01T00:00:00+09:00",
        "end_time": "2026-01-01T16:40:00+09:00",
        "max_points": 100,
    })
    assert resp.status_code == 200
    out = resp.json()
    assert len(out) == 2  # 3행 → 2버킷

    # collected_at desc: 첫 항목이 늦은 버킷
    assert out[0]["cpu"] == 60.0
    assert out[1]["cpu"] == 15.0  # (10+20)/2
    # 버킷 대표 시각은 버킷 내 최소 collected_at
    assert out[1]["collected_at"].startswith("2026-01-01T00:00:10")
    # interface_mbps는 버킷 내 마지막 행의 값
    assert out[1]["interface_mbps"] == {"eth0": {"in_mbps": 1.5, "out_mbps": 0.5}}
    # 스키마 필드 유지
    for key in ("id", "proxy_id", "mem", "disk", "collected_at"):
        assert key in out[0]


def test_history_short_range_returns_raw_rows(client, seeded):
    """버킷 폭 60초 미만이면 원본 행 그대로 (하위호환 경로)."""
    rows = client.get("/api/history", params={
        "proxy_id": seeded["p1"], "max_points": 20000,
    }).json()
    assert len(rows) == 6  # 집계 없이 원본 6행
    assert sorted(r["cpu"] for r in rows) == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]


def test_stats_contract(client, seeded):
    s = client.get("/api/resource-usage/stats").json()
    assert s["total_count"] == 7
    assert s["retention_days"] == 90
    assert s["records_by_proxy"][str(seeded["p1"])] == 6
    assert s["records_by_proxy"][str(seeded["p2"])] == 1
    assert s["oldest_record"] is not None and s["newest_record"] is not None
