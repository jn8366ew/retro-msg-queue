"""FastAPI — POST /jobs, GET /jobs/{job_id} (dev-plan §5).

핸들러는 동기 def. 스레드풀에서 실행되고 이벤트 루프에서 블로킹 DB 호출을 하지 않는다.
API는 브로커에 연결하지 않는다 (E0 대조군 경로는 3단계에서 플래그 뒤에 추가).
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import init_db, session_scope, wait_for_db
from .logfmt import configure_logging, kv
from .models import Job, OutboxEvent

configure_logging()
log = logging.getLogger("app.api")


class JobCreate(BaseModel):
    request_key: str = Field(min_length=1, max_length=100)
    value: int


class OutboxInfo(BaseModel):
    event_id: int
    status: str
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    published_at: datetime | None


class JobDetail(BaseModel):
    job_id: int
    status: str
    input: dict
    result: dict | None
    created_at: datetime
    completed_at: datetime | None
    outbox: OutboxInfo | None  # E0(LEGACY_INLINE_PUBLISH) 행에서만 None


class JobAccepted(BaseModel):
    job_id: int
    status: str


def _detail(job: Job, event: OutboxEvent | None) -> JobDetail:
    return JobDetail(
        job_id=job.id,
        status=job.status,
        input=job.input,
        result=job.result,
        created_at=job.created_at,
        completed_at=job.completed_at,
        outbox=(
            OutboxInfo(
                event_id=event.id,
                status=event.status,
                attempts=event.attempts,
                next_attempt_at=event.next_attempt_at,
                last_error=event.last_error,
                published_at=event.published_at,
            )
            if event is not None
            else None
        ),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    wait_for_db()
    init_db()
    yield


app = FastAPI(title="retro-msg-queue", lifespan=lifespan)


def _create_legacy(input_doc: dict, request_key: str) -> JSONResponse:
    """E0 대조군 — 아웃박스 없는 과거 방식. 정상 경로가 아니다 (R12).

    커밋 후 API가 직접 apply_async 한다. 커밋과 발행 사이에 중단되면 jobs 행만 남고
    발행 의도 기록도 오류 로그도 없다 — 면접 정리 8.7절 "탐지 근거가 없었다"의 재현.
    """
    with session_scope() as s:
        job = Job(request_key=request_key, input=input_doc, status="PENDING")
        s.add(job)
        s.flush()
        job_id = job.id
    log.info(kv("legacy_committed", job_id=job_id, request_key=request_key))

    if settings.api_crash_after_commit:
        log.error(kv("injected_crash", job_id=job_id, flag="API_CRASH_AFTER_COMMIT"))
        os._exit(1)  # 커밋 후·발행 전 중단. 응답도 발행도 없다

    from .tasks import send_compute  # 정상 경로는 브로커를 임포트하지 않는다

    task_id = send_compute(job_id, None)  # event_id 없음 — 아웃박스가 없으니까
    log.info(kv("legacy_published", job_id=job_id, task_id=task_id))
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "PENDING"})


@app.post("/jobs", response_model=JobAccepted, status_code=202, responses={200: {"model": JobDetail}})
def create_job(body: JobCreate):
    input_doc = {"value": body.value}
    try:
        if settings.legacy_inline_publish:
            return _create_legacy(input_doc, body.request_key)

        with session_scope() as s:
            job = Job(request_key=body.request_key, input=input_doc, status="PENDING")
            s.add(job)
            s.flush()  # jobs INSERT — request_key 유니크 충돌은 여기서 IntegrityError
            if settings.api_crash_before_outbox:
                log.error(kv("injected_crash", job_id=job.id, flag="API_CRASH_BEFORE_OUTBOX"))
                raise RuntimeError("injected: API_CRASH_BEFORE_OUTBOX=1")  # → 500, 전체 롤백
            event = OutboxEvent(job_id=job.id, event_type="COMPUTE_JOB", status="PENDING")
            s.add(event)
            s.flush()
            job_id, event_id = job.id, event.id
        log.info(kv("accepted", job_id=job_id, event_id=event_id, request_key=body.request_key))
        return JSONResponse(status_code=202, content={"job_id": job_id, "status": "PENDING"})
    except IntegrityError:
        pass  # 롤백은 session_scope가 했다. 사전 SELECT 없이 충돌 후 재조회 (§0 v2 #5)

    with session_scope() as s:
        existing = s.scalar(select(Job).where(Job.request_key == body.request_key))
        if existing is None:  # 충돌 직후 삭제된 경우 — MVP에서는 발생 경로 없음
            raise HTTPException(status_code=500, detail="request_key conflict but row not found")
        if existing.input != input_doc:
            log.info(kv("duplicate_conflict", job_id=existing.id, request_key=body.request_key))
            raise HTTPException(status_code=409, detail="request_key already used with different input")
        event = s.scalar(select(OutboxEvent).where(OutboxEvent.job_id == existing.id))
        log.info(kv("duplicate_same", job_id=existing.id, request_key=body.request_key))
        return JSONResponse(status_code=200, content=_detail(existing, event).model_dump(mode="json"))


@app.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: int):
    with session_scope() as s:
        row = s.execute(
            select(Job, OutboxEvent)
            .outerjoin(OutboxEvent, OutboxEvent.job_id == Job.id)
            .where(Job.id == job_id)
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="job not found")
        job, event = row
        return _detail(job, event)
