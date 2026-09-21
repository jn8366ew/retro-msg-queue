"""compute 태스크와 send_compute (dev-plan §6).

계산은 반복될 수 있다. 보장하는 것은 같은 업무의 완료 결과가 한 번 채택되고
덮어써지지 않는 것이다 — 조건부 UPDATE의 rowcount로 판정한다.
"""

import logging
import os
import time
from uuid import uuid4

from sqlalchemy import func, update

from .celery_app import app
from .config import settings
from .db import session_scope
from .logfmt import kv
from .models import Job, JobExecution

log = logging.getLogger("app.tasks")


def adopt_result(session, job_id: int, result: dict) -> int:
    """조건부 UPDATE로 결과를 채택한다. 1이면 채택, 0이면 이미 다른 실행이 채택함.

    보장은 여기에 있다. 태스크 앞쪽의 status 사전 조회는 계산 생략 최적화일 뿐이다
    (면접 정리 3절 "사전 조회만으로 동시 실행을 막을 수 없다").
    """
    return session.execute(
        update(Job)
        .where(Job.id == job_id, Job.status == "PENDING")
        .values(status="DONE", result=result, completed_at=func.now())
    ).rowcount


@app.task(bind=True, name="jobs.compute")
def compute(self, job_id: int, event_id: int | None) -> dict:
    execution_id = str(uuid4())
    task_id = self.request.id
    ctx = {"job_id": job_id, "event_id": event_id, "execution_id": execution_id, "task_id": task_id}
    log.info(kv("start", **ctx))

    # 이 블록이 커밋돼야 계산을 시작한다 — 이후 어디서 죽든 시작 기록은 남는다 (R20)
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            # 없는 업무를 성공 처리하지 않는다. result backend가 없으므로 이 실패는 워커 stderr에만 남는다.
            log.error(kv("job_not_found", **ctx))
            raise ValueError(f"job {job_id} not found")
        execution = JobExecution(execution_id=execution_id, job_id=job_id, task_id=task_id)
        s.add(execution)
        if job.status == "DONE":  # 계산 생략 최적화 — 보장이 아니다
            execution.finished_at = func.now()
            execution.outcome = "already_done"
            log.info(kv("already_done", **ctx))
            return {"adopted": False, "result": job.result}
        value = job.input["value"]

    time.sleep(settings.task_delay_sec)
    result = {"value": value, "square": value * value, "execution_id": execution_id}

    if settings.task_crash_before_adopt:
        # E9 독약 메시지 (R19). 자식만 죽는다 — 재전달 여부는 acks_late × reject_on_worker_lost가 정한다
        log.error(kv("injected_crash", flag="TASK_CRASH_BEFORE_ADOPT", **ctx))
        os._exit(1)

    with session_scope() as s:
        rowcount = adopt_result(s, job_id, result)
        # 끝 기록은 채택과 같은 트랜잭션 — 채택됐는데 "끝나지 않은 실행"으로 남는 일이 없다
        _finish(s, execution_id, "adopted" if rowcount == 1 else "rejected")
        if rowcount == 1:
            log.info(kv("adopted", **ctx))
            return {"adopted": True, "result": result}
        job = s.get(Job, job_id)
        log.info(kv("rejected_already_done", **ctx))
        return {"adopted": False, "result": job.result}


def _finish(session, execution_id: str, outcome: str) -> None:
    session.execute(
        update(JobExecution)
        .where(JobExecution.execution_id == execution_id)
        .values(finished_at=func.now(), outcome=outcome)
    )


def send_compute(job_id: int, event_id: int | None) -> str:
    """발행자·run.py republish·E0 경로가 공유하는 전송 함수.

    task_id는 Celery 기본(uuid4)에 맡긴다. 같은 task_id 재사용으로 중복 제거를 기대하지 않는다.

    브로커가 죽었을 때 빨리 실패하도록 전용 연결을 쓴다 (R9). `task_publish_retry=False`는
    publish 래퍼만 끄고, 연결 수립은 kombu `retry_over_time`이 따로 재시도한다. 실측값은
    README 결정 기록 참조. `broker_connection_retry*`는 워커 전용이라 여기에 영향이 없다.
    전용 연결이므로 워커의 재접속 정책은 건드리지 않는다.
    """
    args = [job_id, event_id]
    if settings.publish_connect_max_retries is None:  # 덮어쓰지 않음 — 스펙 기본 동작
        return compute.apply_async(args=args, queue=settings.celery_queue).id

    options = {"max_retries": settings.publish_connect_max_retries}
    with app.connection_for_write(transport_options=options) as conn:
        return compute.apply_async(args=args, queue=settings.celery_queue, connection=conn).id
