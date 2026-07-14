"""데이터 보존 정책(retention) 테스트 — 1차 안정화 회귀 방지."""
from datetime import timedelta

from app.models.proxy import Proxy
from app.models.resource_usage import ResourceUsage
from app.models.traffic_log import TrafficLog
from app.services.resource_collector import (
    enforce_resource_usage_retention,
    enforce_traffic_log_retention,
)
from app.utils.time import now_kst


def test_resource_usage_retention_deletes_only_expired(db_session):
    p = Proxy(host="10.9.4.1", port=22)
    db_session.add(p)
    db_session.commit()

    now = now_kst()
    db_session.add(ResourceUsage(proxy_id=p.id, cpu=1.0,
                                 collected_at=now - timedelta(days=91),
                                 created_at=now, updated_at=now))
    db_session.add(ResourceUsage(proxy_id=p.id, cpu=2.0,
                                 collected_at=now - timedelta(days=89),
                                 created_at=now, updated_at=now))
    db_session.commit()

    enforce_resource_usage_retention(db_session, days=90)

    remain = db_session.query(ResourceUsage).all()
    assert len(remain) == 1
    assert remain[0].cpu == 2.0


def test_traffic_log_retention_expired_and_orphan(db_session):
    p = Proxy(host="10.9.4.2", port=22)
    db_session.add(p)
    db_session.commit()

    now = now_kst()
    db_session.add(TrafficLog(proxy_id=p.id, collected_at=now - timedelta(days=8)))   # 만료
    db_session.add(TrafficLog(proxy_id=p.id, collected_at=now))                        # 유지
    db_session.add(TrafficLog(proxy_id=99999, collected_at=now))                       # 고아
    db_session.commit()

    enforce_traffic_log_retention(db_session, days=7)

    remain = db_session.query(TrafficLog).all()
    assert len(remain) == 1
    assert remain[0].proxy_id == p.id
