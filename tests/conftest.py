"""pytest는 app_test DB에서만 돈다 (R5).

실행: docker compose exec -e DATABASE_URL=postgresql+psycopg://app:app@postgres:5432/app_test api pytest -q
"""

import os

import pytest

_url = os.environ.get("DATABASE_URL", "")
if not _url.rstrip("/").endswith("/app_test"):
    pytest.exit(f"refusing to run: DATABASE_URL must point to app_test (got {_url!r})", returncode=2)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    # with 블록 진입 시 lifespan(wait_for_db → init_db) 실행. 500을 응답으로 받기 위해 raise_server_exceptions=False.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_tables(client):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE outbox_events, jobs RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def counts():
    def _counts() -> tuple[int, int]:
        with engine.connect() as conn:
            jobs = conn.scalar(text("SELECT count(*) FROM jobs"))
            outbox = conn.scalar(text("SELECT count(*) FROM outbox_events"))
        return jobs, outbox

    return _counts
