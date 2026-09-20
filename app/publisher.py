"""아웃박스 발행자 — 독립 루프 (dev-plan §6).

Celery beat로 스케줄링하지 않는다. 브로커가 죽어도 DB 조회를 계속하는 독립 프로세스다.
최대 시도 초과로 이벤트를 삭제하거나 상태를 바꾸지 않는다. 영구 오류도 last_error에 남기고
사용자가 원인을 고치거나 발행자를 멈춘다.
"""

import logging
import os
import signal
import time
from types import FrameType

from sqlalchemy import func, select, text, update

from .config import settings
from .db import init_db, session_scope, wait_for_db
from .logfmt import configure_logging, kv
from .models import OutboxEvent
from .tasks import send_compute

configure_logging()
log = logging.getLogger("app.publisher")

_stop = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    """현재 이벤트 처리를 마친 뒤 루프를 빠져나간다. 강제 중단 실험은 docker compose kill."""
    global _stop
    _stop = True
    log.info(kv("signal_received", signal=signal.Signals(signum).name))


def fetch_pending(session, limit: int) -> list[tuple[int, int]]:
    """미발행 이벤트 조회. 후속: 다중 발행자 시 FOR UPDATE SKIP LOCKED (dev-plan §13-1)."""
    rows = session.execute(
        select(OutboxEvent.id, OutboxEvent.job_id)
        .where(OutboxEvent.status == "PENDING", OutboxEvent.next_attempt_at <= func.now())
        .order_by(OutboxEvent.id)
        .limit(limit)
    ).all()
    return [(r.id, r.job_id) for r in rows]


def publish_one(event_id: int, job_id: int) -> None:
    # [tx2] 시도 기록을 먼저 올린다. 호출 전에 증가시키므로 실제 브로커 수신 횟수와 같지 않다.
    with session_scope() as s:
        s.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id)
            .values(
                attempts=OutboxEvent.attempts + 1,
                next_attempt_at=func.now()
                + text(f"interval '{settings.publish_retry_delay_sec} seconds'"),
            )
        )
        attempt = s.scalar(select(OutboxEvent.attempts).where(OutboxEvent.id == event_id))

    ctx = {"job_id": job_id, "event_id": event_id, "attempt": attempt}

    # [트랜잭션 밖] 브로커 전송
    try:
        task_id = send_compute(job_id, event_id)
    except Exception as exc:  # kombu는 드라이버 예외를 OperationalError로 감싼다
        err = f"{type(exc).__name__}: {exc}"[:500]
        with session_scope() as s:
            s.execute(
                update(OutboxEvent).where(OutboxEvent.id == event_id).values(last_error=err)
            )
        log.warning(kv("publish_failed", error=err, **ctx))
        return

    log.info(kv("published", task_id=task_id, **ctx))

    if settings.publisher_crash_after_send:
        log.error(kv("injected_crash", task_id=task_id, flag="PUBLISHER_CRASH_AFTER_SEND", **ctx))
        os._exit(1)  # SENT 기록 전 중단 — E5

    # [tx4] 발행 성공 기록
    with session_scope() as s:
        s.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id == event_id, OutboxEvent.status == "PENDING")
            .values(status="SENT", published_at=func.now())
        )
    log.info(kv("marked_sent", task_id=task_id, **ctx))


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    wait_for_db()
    init_db()
    log.info(
        kv(
            "publisher_start",
            broker_kind=settings.broker_kind,
            queue=settings.celery_queue,
            interval=settings.publish_interval_sec,
            batch=settings.publish_batch_size,
            retry_delay=settings.publish_retry_delay_sec,
            crash_after_send=int(settings.publisher_crash_after_send),
        )
    )

    while not _stop:
        try:
            with session_scope() as s:
                events = fetch_pending(s, settings.publish_batch_size)
            for event_id, job_id in events:
                if _stop:
                    break
                publish_one(event_id, job_id)
        except Exception as exc:  # DB 오류 등. 로그 후 다음 주기로
            log.exception(kv("loop_error", error=f"{type(exc).__name__}: {exc}"))
        time.sleep(settings.publish_interval_sec)

    log.info(kv("publisher_stop"))


if __name__ == "__main__":
    main()
