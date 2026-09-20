"""engine, session_scope(), wait_for_db(), init_db()."""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

from .config import settings

log = logging.getLogger("app.db")

# api·publisher·worker가 동시에 create_all을 부를 때 직렬화용 (R1). 임의 상수.
INIT_DB_LOCK_KEY = 987654321


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """요청·배치·태스크 단위 세션. 정상 종료 시 commit, 예외 시 rollback 후 re-raise."""
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def wait_for_db(timeout_sec: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_sec
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            log.info("db_ready attempt=%d", attempt)
            return
        except Exception as exc:  # noqa: BLE001 - 기동 대기는 어떤 오류든 재시도
            if time.monotonic() >= deadline:
                raise
            log.info("db_wait attempt=%d error=%s", attempt, type(exc).__name__)
            time.sleep(1)


def init_db() -> None:
    """테이블 생성. advisory lock으로 프로세스 간 직렬화 (R1). 여러 번 불러도 안전."""
    from . import models  # noqa: F401 - 테이블 등록

    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": INIT_DB_LOCK_KEY})
        Base.metadata.create_all(conn)
    log.info("db_init_done tables=%s", ",".join(Base.metadata.tables))
