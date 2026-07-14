"""시작 시 DB 검사/복구(_recover_sqlite_db) 회귀 테스트 — 1차 안정화 핵심.

특히 "비정상 종료 후 WAL의 커밋 데이터가 보존되는가"를 고정한다
(과거 버전은 WAL을 선삭제해 데이터 유실·손상을 유발했음).
"""
import os
import shutil
import sqlite3
import time

from app.main import _recover_sqlite_db


def _make_wal_db(path: str):
    """WAL에 미체크포인트 커밋 데이터가 있는 DB를 만든다(크래시 시뮬레이션)."""
    src = str(path) + ".src"
    conn = sqlite3.connect(src)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('in-wal')")
    conn.commit()
    # 연결을 닫지 않은 채 파일 복사 → close 시 자동 체크포인트를 회피
    shutil.copy(src, path)
    shutil.copy(src + "-wal", str(path) + "-wal")
    shutil.copy(src + "-shm", str(path) + "-shm")
    conn.close()


def test_wal_data_preserved_after_crash(tmp_path):
    db = str(tmp_path / "crash.db")
    _make_wal_db(db)
    assert os.path.getsize(db + "-wal") > 0

    _recover_sqlite_db(f"sqlite:///{db}")

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT v FROM t").fetchall()
    conn.close()
    assert rows == [("in-wal",)]
    assert not os.path.exists(db + ".corrupt")  # 정상 DB는 복구 경로 미진입


def test_quick_check_marker_skips_within_24h(tmp_path):
    db = str(tmp_path / "marker.db")
    sqlite3.connect(db).execute("CREATE TABLE t (id INTEGER PRIMARY KEY)").connection.commit()

    _recover_sqlite_db(f"sqlite:///{db}")
    marker = db + ".lastcheck"
    assert os.path.exists(marker)

    mtime = os.path.getmtime(marker)
    time.sleep(0.05)
    _recover_sqlite_db(f"sqlite:///{db}")
    assert os.path.getmtime(marker) == mtime  # 24시간 내 재검사 생략


def test_corrupted_db_recovered_to_usable_state(tmp_path):
    db = str(tmp_path / "corrupt.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row{i}",) for i in range(500)])
    conn.commit()
    conn.close()

    size = os.path.getsize(db)
    with open(db, "r+b") as f:
        f.seek(size // 2)
        f.write(b"\xde\xad\xbe\xef" * 256)

    _recover_sqlite_db(f"sqlite:///{db}")

    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    conn.close()
    assert os.path.exists(db + ".corrupt")  # 손상 원본 보존


def test_non_sqlite_url_is_noop(tmp_path):
    _recover_sqlite_db("postgresql://u:p@localhost/db")  # 예외 없이 통과


def test_missing_file_is_noop(tmp_path):
    db = str(tmp_path / "nope.db")
    _recover_sqlite_db(f"sqlite:///{db}")
    assert not os.path.exists(db)  # 파일을 만들지 않음
