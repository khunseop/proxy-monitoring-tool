"""resource_analysis 서비스 수치 계약 테스트.

로드맵 Phase 2-4(쿼리 1회화 리팩터링)의 "결과 수치 완전 불변" 계약을 고정한다.
"""
from datetime import timedelta

import pytest
from app.models.proxy import Proxy
from app.models.resource_usage import ResourceUsage
from app.services.resource_analysis import (
    _percentile,
    compute_percentiles,
    compute_time_in_band,
)
from app.utils.time import now_kst


def test_percentile_interpolation():
    vals = [float(v) for v in range(1, 101)]  # 1..100
    assert _percentile(vals, 50) == pytest.approx(50.5)
    assert _percentile(vals, 95) == pytest.approx(95.05)
    assert _percentile(vals, 99) == pytest.approx(99.01)
    assert _percentile(vals, 100) == 100.0
    assert _percentile([], 50) == 0.0
    assert _percentile([7.0], 95) == 7.0


@pytest.fixture()
def seeded(db_session):
    p = Proxy(host="10.9.3.1", port=22)
    db_session.add(p)
    db_session.commit()

    base = now_kst() - timedelta(hours=2)
    # cpu: 10,20,...,100 (10개)
    for i in range(1, 11):
        db_session.add(ResourceUsage(
            proxy_id=p.id, cpu=10.0 * i, mem=None,
            collected_at=base + timedelta(minutes=i),
            created_at=base, updated_at=base,
        ))
    db_session.commit()
    return {"pid": p.id, "db": db_session}


def test_compute_percentiles_known_values(seeded):
    res = compute_percentiles(
        seeded["db"], [seeded["pid"]], None, None,
        business_hours=False, metrics=["cpu", "mem"],
    )
    cpu = next(r for r in res if r["metric"] == "cpu")
    assert cpu["count"] == 10
    assert cpu["p50"] == pytest.approx(55.0)
    assert cpu["mean"] == pytest.approx(55.0)
    assert cpu["max"] == 100.0

    # 값이 전혀 없는 지표는 None 계약
    mem = next(r for r in res if r["metric"] == "mem")
    assert mem["count"] == 0
    assert mem["p50"] is None and mem["max"] is None


def test_compute_percentiles_unknown_proxy_returns_empty_rows(seeded):
    res = compute_percentiles(
        seeded["db"], [99999], None, None, business_hours=False, metrics=["cpu"],
    )
    assert len(res) == 1
    assert res[0]["count"] == 0


def test_compute_time_in_band_distribution(seeded):
    res = compute_time_in_band(
        seeded["db"], [seeded["pid"]], None, None,
        business_hours=False, metric="cpu",
    )
    assert len(res) == 1
    r = res[0]
    assert r["total_samples"] == 10
    bands = {b["label"]: b["count"] for b in r["bands"]}
    # 10,20 → 0~30 / 30,40,50 → 30~60 / 60,70 → 60~80 / 80,90 → 80~100 / 100 → 100%+
    assert bands["0~30%"] == 2
    assert bands["30~60%"] == 3
    assert bands["60~80%"] == 2
    assert bands["80~100%"] == 2
    assert bands["100%+"] == 1
    assert sum(bands.values()) == 10
