"""SENT+PENDING 분류와 reconcile (dev-plan R20~R22, 7단계).

E10·E11이 실제 워커 사망으로 보는 것을, 여기서는 실행 기록의 나이와 개수를 직접 만들어 확인한다.
"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

import app.tasks as tasks
from app.config import settings
from app.db import engine, session_scope
from app.models import Job, JobExecution, OutboxEvent
from app.reconcile import classify, reconcile

STALE = 60.0
MAX = 3


def _job(key: str, job_status: str = "PENDING", outbox_status: str = "SENT") -> int:
    with session_scope() as s:
        job = Job(request_key=key, input={"value": 3}, status=job_status)
        s.add(job)
        s.flush()
        s.add(OutboxEvent(job_id=job.id, status=outbox_status, attempts=1))
        return job.id


def _execution(job_id: int, age_sec: float, finished: bool = False) -> None:
    with session_scope() as s:
        s.add(
            JobExecution(
                execution_id=str(uuid4()),
                job_id=job_id,
                task_id="t",
                started_at=func.now() - timedelta(seconds=age_sec),
                finished_at=func.now() if finished else None,
                outcome="rejected" if finished else None,
            )
        )


def _states() -> dict[int, str]:
    with engine.connect() as conn:
        return {r["job_id"]: r["state"] for r in classify(conn, STALE, MAX)}


def _outbox(job_id: int) -> OutboxEvent:
    with session_scope() as s:
        return s.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job_id))


@pytest.fixture
def four_states():
    not_started = _job("not-started")
    running = _job("running")
    _execution(running, age_sec=5)
    stalled = _job("stalled")
    _execution(stalled, age_sec=120)
    gave_up = _job("gave-up")
    for age in (200, 150, 100):
        _execution(gave_up, age_sec=age)
    return not_started, running, stalled, gave_up


def test_classify_four_states(four_states):
    not_started, running, stalled, gave_up = four_states
    assert _states() == {
        not_started: "not_started",
        running: "running",
        stalled: "stalled",
        gave_up: "gave_up",
    }


def test_only_sent_and_pending_are_classified():
    _job("done", job_status="DONE")
    _job("unsent", outbox_status="PENDING")
    assert _states() == {}


def test_gave_up_counts_unfinished_only():
    job = _job("two-dead")
    _execution(job, age_sec=300)
    _execution(job, age_sec=200)
    _execution(job, age_sec=250, finished=True)
    assert _states() == {job: "stalled"}
    _execution(job, age_sec=100)
    assert _states() == {job: "gave_up"}


def test_reconcile_returns_only_stalled_to_pending(four_states):
    not_started, running, stalled, gave_up = four_states
    with engine.begin() as conn:
        assert reconcile(conn, STALE, MAX) == [stalled]

    ob = _outbox(stalled)
    assert (ob.status, ob.published_at, ob.last_error) == ("PENDING", None, "reconciled: stalled")
    for untouched in (not_started, running, gave_up):
        assert _outbox(untouched).status == "SENT"


def test_reconcile_twice_is_noop(four_states):
    with engine.begin() as conn:
        reconcile(conn, STALE, MAX)
    with engine.begin() as conn:
        assert reconcile(conn, STALE, MAX) == []


@pytest.fixture
def fast_task(monkeypatch):
    monkeypatch.setattr(settings, "task_delay_sec", 0)


def _executions(job_id: int) -> list[JobExecution]:
    with session_scope() as s:
        return list(
            s.scalars(
                select(JobExecution)
                .where(JobExecution.job_id == job_id)
                .order_by(JobExecution.started_at)
            )
        )


def test_task_records_start_and_outcome(fast_task):
    job = _job("task-run", outbox_status="SENT")
    tasks.compute.apply(args=[job, 1], task_id="task-1")
    tasks.compute.apply(args=[job, 1], task_id="task-2")

    rows = _executions(job)
    assert [(r.task_id, r.outcome) for r in rows] == [("task-1", "adopted"), ("task-2", "already_done")]
    assert all(r.finished_at is not None for r in rows)


def test_start_is_committed_before_compute(fast_task, monkeypatch):
    """계산 중에 죽으면 시작 기록만 남는다 — 이것이 stalled 판정의 근거다."""

    def boom(_):
        raise RuntimeError("died mid-compute")

    monkeypatch.setattr(tasks, "time", SimpleNamespace(sleep=boom))
    job = _job("task-crash", outbox_status="SENT")
    tasks.compute.apply(args=[job, 1], task_id="task-crash")

    [row] = _executions(job)
    assert (row.task_id, row.finished_at, row.outcome) == ("task-crash", None, None)
    assert _states() == {job: "running"}
