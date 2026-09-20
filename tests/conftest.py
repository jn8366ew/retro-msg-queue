"""pytest는 app_test DB에서만 돈다 (R5).

실행: docker compose exec api pytest -q

`DATABASE_URL`이 무엇을 가리키든 아래에서 DB 이름을 app_test로 바꾼 뒤 app.* 를 임포트한다.
엔진은 app/db.py 임포트 시점에 만들어지므로, 이 치환이 임포트보다 먼저다.
실험 DB(app)를 건드릴 경로 자체를 없앤다.
"""

import os

import pytest
from sqlalchemy.engine import make_url

_raw = os.environ.get("DATABASE_URL", "")
if not _raw:
    pytest.exit("DATABASE_URL is required", returncode=2)

os.environ["DATABASE_URL"] = (
    make_url(_raw).set(database="app_test").render_as_string(hide_password=False)
)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402

if engine.url.database != "app_test":  # 치환이 엔진에 반영됐는지 확인
    pytest.exit(f"refusing to run: engine points at {engine.url.database!r}", returncode=2)


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
