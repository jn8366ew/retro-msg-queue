"""Celery 앱. BROKER_KIND로 redis/sqs 분기 (dev-plan §7).

result backend 없음. 상태는 공통 DB에서만 조회한다.
"""

import logging

from celery import Celery

from .config import settings

log = logging.getLogger("app.celery")


def _broker_url_and_options() -> tuple[str, dict]:
    if settings.broker_kind == "redis":
        return settings.redis_url, {"visibility_timeout": 3600}

    if settings.broker_kind == "sqs":
        # 큐를 생성하지 않는다. 기존 큐를 predefined_queues로 참조한다.
        # 키는 Celery 큐 이름이어야 한다 — 불일치 시 UndefinedQueueException (R14).
        if not settings.sqs_region or not settings.sqs_queue_url:
            raise RuntimeError("BROKER_KIND=sqs requires SQS_REGION and SQS_QUEUE_URL")
        if not settings.sqs_queue_url.rstrip("/").endswith(f"/{settings.celery_queue}"):
            raise RuntimeError(
                f"SQS_QUEUE_URL must end with /{settings.celery_queue} "
                f"(CELERY_QUEUE), got {settings.sqs_queue_url}"
            )
        return "sqs://", {
            "region": settings.sqs_region,
            "predefined_queues": {settings.celery_queue: {"url": settings.sqs_queue_url}},
            "polling_interval": 1,
            "wait_time_seconds": 10,
        }

    raise RuntimeError(f"BROKER_KIND must be 'redis' or 'sqs', got {settings.broker_kind!r}")


broker_url, broker_transport_options = _broker_url_and_options()

app = Celery("retro_msg_queue", broker=broker_url, include=["app.tasks"])

app.conf.update(
    broker_transport_options=broker_transport_options,
    task_default_queue=settings.celery_queue,
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    result_backend=None,
    # 기본값 명시. 중단 복구 실험(E8)은 후속 — dev-plan §13-7
    task_acks_late=False,
    task_reject_on_worker_lost=False,
    worker_prefetch_multiplier=1,
    # 발행자 루프가 재시도를 담당한다. 연결 수립 재시도는 이것과 별개 (R9)
    task_publish_retry=False,
    broker_connection_timeout=4,
    # 워커 전용. 워커가 Redis보다 먼저 떠도 재접속한다
    broker_connection_retry_on_startup=True,
)

log.info(
    "celery_configured broker_kind=%s queue=%s transport_options=%s",
    settings.broker_kind,
    settings.celery_queue,
    broker_transport_options,
)
