from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
import os
import time as _time
from dotenv import load_dotenv
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database.database import engine
from app.models import proxy, proxy_group
from app.models import resource_usage as resource_usage_model
from app.models import resource_config as resource_config_model
from app.models import session_browser_config as session_browser_config_model
from app.models import traffic_log as traffic_log_model
from app.api import proxies, proxy_groups, config_management
from app.api import resource_usage as resource_usage_api
from app.api import resource_config as resource_config_api
from app.api import session_browser as session_browser_api
from app.api import session_browser_config as session_browser_config_api
from app.api import traffic_logs as traffic_logs_api
from app.api import resource_analysis as resource_analysis_api
from fastapi_standalone_docs import StandaloneDocs
import warnings
from sqlalchemy import text, inspect
from sqlalchemy.exc import OperationalError
try:
    from cryptography.utils import CryptographyDeprecationWarning
    warnings.filterwarnings("ignore", category=CryptographyDeprecationWarning)
except Exception:
    pass
from app.database.database import SessionLocal
from app.models.proxy import Proxy as ProxyModel
from app.utils.crypto import encrypt_string
from app.utils.path_resolver import get_templates_dir, get_static_dir, get_docs_dir
from app.utils.logging_config import setup_logging

# Load environment variables from .env
load_dotenv(override=False)

# 로깅 초기화 (환경변수 로드 후 가장 먼저 실행)
setup_logging()

import logging as _logging
_startup_logger = _logging.getLogger(__name__)


def _recover_sqlite_db(db_url: str) -> None:
    """SQLite DB 손상 여부를 확인하고 필요 시 자동 복구를 시도합니다.

    주의: WAL/SHM 파일은 삭제하지 않는다. WAL에는 아직 메인 DB에
    체크포인트되지 않은 커밋 데이터가 있으므로, DB를 정상적으로 열면
    SQLite가 자동으로 복구(재적용)한다.
    """
    import sqlite3, shutil, re, time

    # sqlite:///./path.db 또는 sqlite:////abs/path.db 형태에서 파일 경로 추출
    m = re.match(r"sqlite:///(.+)", db_url)
    if not m:
        return
    db_path = m.group(1)

    if not os.path.exists(db_path):
        return  # 신규 파일이면 복구 불필요

    _startup_logger.info(
        "[DB] 경로: %s (크기: %.1f MB)",
        os.path.abspath(db_path),
        os.path.getsize(db_path) / (1024 * 1024),
    )

    # 1단계: 가벼운 열기 확인. quick_check는 DB 크기에 비례해 시작을
    # 블로킹하므로 24시간에 한 번만 수행한다(마커 파일 mtime 기준).
    check_marker = db_path + ".lastcheck"
    try:
        marker_age_hours = (time.time() - os.path.getmtime(check_marker)) / 3600
    except OSError:
        marker_age_hours = float("inf")

    try:
        conn = sqlite3.connect(db_path, timeout=5)
        try:
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            if marker_age_hours >= 24:
                result = conn.execute("PRAGMA quick_check").fetchone()
                if not result or result[0] != "ok":
                    raise sqlite3.DatabaseError(f"quick_check 실패: {result}")
                with open(check_marker, "w", encoding="utf-8") as f:
                    f.write(str(int(time.time())))
                _startup_logger.info("[DB] quick_check 통과")
            return  # 정상
        finally:
            conn.close()
    except Exception as e:
        _startup_logger.error("[DB] 검사 실패: %s — 복구를 시도합니다.", e)

    # 2단계: backup API로 새 DB 재생성, 실패 시 iterdump() 폴백
    recovered_path = db_path + ".recovered"
    try:
        src = sqlite3.connect(db_path, timeout=5)
        dst = sqlite3.connect(recovered_path, timeout=5)
        try:
            try:
                src.backup(dst)
                # backup API는 손상 페이지도 그대로 복사할 수 있으므로 결과 검증
                result = dst.execute("PRAGMA quick_check").fetchone()
                if not result or result[0] != "ok":
                    raise sqlite3.DatabaseError(f"backup 결과 quick_check 실패: {result}")
            except Exception as backup_err:
                _startup_logger.warning(
                    "[DB] backup API 복구 실패: %s — iterdump로 재시도합니다.", backup_err
                )
                dst.close()
                try:
                    os.remove(recovered_path)
                except OSError:
                    pass
                dst = sqlite3.connect(recovered_path, timeout=5)
                dump_iter = src.iterdump()
                while True:
                    try:
                        line = next(dump_iter)
                    except StopIteration:
                        break
                    except Exception:
                        break  # 손상 지점 이후는 읽을 수 없음 — 여기까지 복구
                    try:
                        dst.execute(line)
                    except Exception:
                        pass  # 손상 행 스킵
                dst.commit()
        finally:
            src.close()
            dst.close()

        corrupt_path = db_path + ".corrupt"
        shutil.move(db_path, corrupt_path)
        shutil.move(recovered_path, db_path)
        # 남은 WAL/SHM 정리
        for ext in ("-wal", "-shm"):
            try:
                os.remove(db_path + ext)
            except OSError:
                pass
        _startup_logger.warning(
            "[DB] DB 복구 완료. 손상 파일: %s → 복구 파일: %s", corrupt_path, db_path
        )
    except Exception as e:
        # 3단계: 복구 불가 → 백업 후 빈 DB로 시작
        _startup_logger.critical("[DB] iterdump 복구 실패: %s — 빈 DB로 새로 시작합니다.", e)
        try:
            shutil.move(db_path, db_path + ".unrecoverable")
        except Exception:
            pass
        try:
            os.remove(recovered_path)
        except OSError:
            pass
        # WAL/SHM도 정리 (새 DB 생성 전 잔재 제거)
        for ext in ("-wal", "-shm"):
            try:
                os.remove(db_path + ext)
            except OSError:
                pass


_recover_sqlite_db(os.getenv("DATABASE_URL", "sqlite:///./pmt.db"))

proxy.Base.metadata.create_all(bind=engine)
resource_usage_model.Base.metadata.create_all(bind=engine)
resource_config_model.Base.metadata.create_all(bind=engine)
session_browser_config_model.Base.metadata.create_all(bind=engine)
traffic_log_model.Base.metadata.create_all(bind=engine)

_app_start_time = _time.monotonic()

app = FastAPI(
    title="PMT",
    description="Proxy Monitoring Tool",
    version="2026.06.18"
)

# 전역 예외 핸들러 — 스택 트레이스 유출 방지
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    _startup_logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"error": "Validation error", "detail": exc.errors()},
    )

# Rate Limiting (slowapi)
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware

    limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
except ImportError:
    _startup_logger.warning("slowapi not installed — rate limiting disabled")
    limiter = None

# Security/CORS middleware (configure via env)
cors_origins = os.getenv("CORS_ALLOW_ORIGINS", "*")
allow_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]
allow_credentials_env = os.getenv("CORS_ALLOW_CREDENTIALS", "false").lower() in {"1", "true", "yes"}
# Starlette forbids wildcard origins with credentials; disable credentials if wildcard is present
allow_credentials = False if ("*" in allow_origins and len(allow_origins) == 1) else allow_credentials_env
if "*" in allow_origins:
    _startup_logger.warning(
        "CORS_ALLOW_ORIGINS is set to wildcard '*'. "
        "Set CORS_ALLOW_ORIGINS to specific origins in production."
    )
from fastapi.middleware.gzip import GZipMiddleware
...
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    response = await call_next(request)
    if "text/html" in response.headers.get("content-type", ""):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
    return response
# Enable docs only when enabled via env (default true)
if os.getenv("ENABLE_DOCS", "true").lower() in {"1", "true", "yes"}:
    StandaloneDocs(app)
    # expose version in app state for templates
app.github_url = os.getenv("GITHUB_URL")

# Prometheus 메트릭
try:
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
except ImportError:
    _startup_logger.warning("prometheus-fastapi-instrumentator not installed — /metrics disabled")

# 템플릿과 정적 파일 설정 (dev/pyinstaller 모두 지원)
templates = Jinja2Templates(directory=get_templates_dir())
app.mount("/static", StaticFiles(directory=get_static_dir()), name="static")
app.mount("/docs-static", StaticFiles(directory=get_docs_dir()), name="docs-static")

# API 라우터
app.include_router(proxies.router, prefix="/api", tags=["proxies"])
app.include_router(proxy_groups.router, prefix="/api", tags=["proxy-groups"])
app.include_router(resource_usage_api.router, prefix="/api", tags=["resource-usage"])
app.include_router(resource_config_api.router, prefix="/api", tags=["resource-config"])
app.include_router(session_browser_api.router, prefix="/api", tags=["session-browser"])
app.include_router(session_browser_config_api.router, prefix="/api", tags=["session-browser-config"])
app.include_router(traffic_logs_api.router, prefix="/api", tags=["traffic-logs"])
app.include_router(config_management.router, prefix="/api", tags=["config"])
app.include_router(resource_analysis_api.router, prefix="/api", tags=["resource-analysis"])

# 페이지 라우터
@app.get("/")
async def read_root(request: Request):
    # 기본 라우트를 자원사용률 페이지로 제공
    return templates.TemplateResponse("components/resource_usage.html", {"request": request})

@app.get("/resource")
async def read_resource(request: Request):
    return templates.TemplateResponse("components/resource_usage.html", {"request": request})

@app.get("/history")
async def read_resource_history(request: Request):
    return templates.TemplateResponse("components/resource_history.html", {"request": request})

@app.get("/history/analysis")
async def read_resource_history_analysis(request: Request):
    return templates.TemplateResponse("components/resource_history.html", {"request": request})

@app.get("/resource-analysis")
async def read_resource_analysis_redirect(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/history/analysis", status_code=301)

@app.get("/resource/history")
async def read_resource_history_redirect(request: Request):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/history", status_code=301)

@app.get("/resource-history")
async def read_resource_history_legacy(request: Request):
    # 레거시 URL 리다이렉트
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/history", status_code=301)

@app.get("/proxy")
async def read_proxy_management(request: Request):
    return templates.TemplateResponse("components/proxy_management.html", {"request": request})

@app.get("/proxy/groups")
async def read_proxy_groups(request: Request):
    return templates.TemplateResponse("components/proxy_management.html", {"request": request})

@app.get("/settings")
async def read_settings(request: Request):
    return templates.TemplateResponse("components/settings.html", {"request": request})

@app.get("/session")
async def read_session(request: Request):
    return templates.TemplateResponse("components/session_browser.html", {"request": request})


@app.get("/healthz")
def healthz():
    from app.utils.background_collector import background_collector
    db_status = "ok"
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
    except Exception as e:
        _startup_logger.error("Health check DB error: %s", e)
        db_status = "error"

    collector_status = "running" if background_collector._retention_task and not background_collector._retention_task.done() else "stopped"
    uptime_seconds = int(_time.monotonic() - _app_start_time)

    status = "ok" if db_status == "ok" else "degraded"
    return {
        "status": status,
        "db": db_status,
        "collector": collector_status,
        "uptime_seconds": uptime_seconds,
        "collection_tasks": background_collector.get_status()["active_count"],
        "last_collect_success_at": background_collector.last_success_at,
        "last_collect_result": background_collector.last_result,
    }


@app.get("/traffic-logs")
async def read_traffic_logs_page(request: Request):
    return templates.TemplateResponse("components/traffic_logs.html", {"request": request})

@app.get("/traffic-logs/upload")
async def read_traffic_logs_upload(request: Request):
    return templates.TemplateResponse("components/traffic_logs.html", {"request": request})


# Minimal security headers
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    return response


# One-time startup task: migrate legacy plaintext proxy passwords to encrypted format
@app.on_event("startup")
def migrate_legacy_proxy_passwords():
    try:
        db = SessionLocal()
        try:
            rows = db.query(ProxyModel).all()
            changed = 0
            for row in rows:
                pw = getattr(row, "password", None)
                if pw and not str(pw).strip().startswith("enc$"):
                    row.password = encrypt_string(pw)
                    changed += 1
            if changed:
                db.commit()
        finally:
            db.close()
    except Exception:
        # avoid breaking startup due to migration failure
        _startup_logger.warning("[Startup] 프록시 비밀번호 마이그레이션 실패", exc_info=True)


# (removed) one-time startup cleanup for legacy accumulated rows

# One-time startup migration: add interval_sec column if missing (existing DB)
@app.on_event("startup")
def migrate_resource_config_interval_sec():
    try:
        db = SessionLocal()
        try:
            db.execute(text("ALTER TABLE resource_config ADD COLUMN interval_sec INTEGER NOT NULL DEFAULT 60"))
            db.commit()
            _startup_logger.info("[DB] resource_config.interval_sec 컬럼 추가 완료")
        except Exception:
            db.rollback()  # 컬럼이 이미 존재하면 무시
        finally:
            db.close()
    except Exception:
        _startup_logger.warning("[Startup] interval_sec 컬럼 마이그레이션 실패", exc_info=True)


# Start retention policy background task on startup
@app.on_event("startup")
async def start_background_tasks():
    from app.utils.background_collector import background_collector
    # Start retention policy task (runs every hour)
    await background_collector.start_retention_policy(interval_sec=3600)
    # Auto-start resource collection using DB config
    await background_collector.start_on_startup()

@app.on_event("shutdown")
async def stop_background_tasks():
    from app.utils.background_collector import background_collector
    from app.utils.ssh import ssh_pool
    # Stop retention policy task
    await background_collector.stop_retention_policy()
    # Close pooled SSH connections
    ssh_pool.close_all()
