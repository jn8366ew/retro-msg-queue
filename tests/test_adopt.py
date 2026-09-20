"""조건부 UPDATE 채택 로직 (dev-plan §6).

E6b(concurrency=2)가 실제 동시 실행으로 확인하는 것을, 여기서는 순서를 강제해 단위로 확인한다.
보장은 rowcount에 있고 태스크 앞쪽의 status 사전 조회는 최적화일 뿐이라는 것이 요점이다.
"""

from sqlalchemy import select

from app.db import session_scope
from app.models import Job
from app.tasks import adopt_result


def _make_job(request_key: str = "adopt-1", value: int = 6) -> int:
    with session_scope() as s:
        job = Job(request_key=request_key, input={"value": value}, status="PENDING")
        s.add(job)
        s.flush()
        return job.id


def test_first_adopt_wins_second_is_rejected(client):
    job_id = _make_job()
    first = {"value": 6, "square": 36, "execution_id": "exec-A"}
    second = {"value": 6, "square": 36, "execution_id": "exec-B"}

    with session_scope() as s:
        assert adopt_result(s, job_id, first) == 1

    with session_scope() as s:
        assert adopt_result(s, job_id, second) == 0  # 이미 DONE — 조건 불일치

    with session_scope() as s:
        job = s.get(Job, job_id)
        assert job.status == "DONE"
        assert job.result["execution_id"] == "exec-A"  # 나중 실행이 덮어쓰지 못한다
        assert job.completed_at is not None


def test_adopt_sets_completed_at_and_result(client):
    job_id = _make_job(request_key="adopt-2", value=9)
    with session_scope() as s:
        assert adopt_result(s, job_id, {"value": 9, "square": 81, "execution_id": "x"}) == 1

    with session_scope() as s:
        job = s.get(Job, job_id)
        assert job.result == {"value": 9, "square": 81, "execution_id": "x"}


def test_adopt_unknown_job_returns_zero(client):
    with session_scope() as s:
        assert adopt_result(s, 999999, {"value": 1, "square": 1, "execution_id": "x"}) == 0


def test_send_compute_signature_is_importable(client):
    """2단계 코드가 임포트 가능한지 — celery_app 설정 오류를 테스트에서 먼저 잡는다."""
    from app.celery_app import app as celery_app
    from app.tasks import compute, send_compute  # noqa: F401

    assert celery_app.conf.task_ignore_result is True
    assert celery_app.conf.task_acks_late is False
    assert celery_app.conf.task_publish_retry is False
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert compute.name == "jobs.compute"


def test_jobs_table_roundtrip(client):
    job_id = _make_job(request_key="adopt-3", value=2)
    with session_scope() as s:
        found = s.scalar(select(Job).where(Job.id == job_id))
        assert found.input == {"value": 2}
        assert found.status == "PENDING"
