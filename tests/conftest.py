"""공통 테스트 픽스처 — 인메모리 SQLite DB + TestClient"""
import os

# app 모듈 import 전에 설정해야 함 — 테스트가 실제 ./pmt.db와 ./logs를 건드리지 않도록
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("LOG_TO_CONSOLE", "false")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sqlalchemy.pool import StaticPool
from app.database.database import get_db, Base

# 모든 모델을 임포트해서 Base.metadata에 등록
import app.models.proxy  # noqa: F401
import app.models.proxy_group  # noqa: F401
import app.models.resource_usage  # noqa: F401
import app.models.resource_config  # noqa: F401
import app.models.session_browser_config  # noqa: F401
import app.models.traffic_log  # noqa: F401

# StaticPool: 인메모리 SQLite에서 모든 연결이 같은 DB를 공유
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def _get_test_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture()
def client():
    from app.main import app
    app.dependency_overrides[get_db] = _get_test_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def db_session():
    """데이터 시딩용 세션. 테스트 종료 시 시딩 대상 테이블을 비워 테스트 간 격리."""
    from app.models.traffic_log import TrafficLog
    from app.models.resource_usage import ResourceUsage
    from app.models.proxy import Proxy

    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.rollback()
        for model in (TrafficLog, ResourceUsage, Proxy):
            db.query(model).delete(synchronize_session=False)
        db.commit()
        db.close()
