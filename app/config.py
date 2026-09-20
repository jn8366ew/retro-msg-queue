"""환경변수 로드·검증. pydantic-settings 대신 os.environ (R4).

장애 주입 플래그는 요청 시점에 settings 속성으로 읽는다 — 테스트에서 monkeypatch 가능.
"""

import os
from dataclasses import dataclass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).split("#", 1)[0].strip()


def _flag(name: str) -> bool:
    return _env(name, "0").lower() in ("1", "true", "yes")


@dataclass
class Settings:
    database_url: str
    # 로컬 실험 전용 장애 주입 (§0 v2 #1, #2) + E0 대조군 (R12)
    api_crash_before_outbox: bool
    publisher_crash_after_send: bool
    legacy_inline_publish: bool
    api_crash_after_commit: bool
    # 2단계 이후 — 정의만. 검증은 celery_app/publisher에서 (§7, §9)
    broker_kind: str
    redis_url: str
    celery_queue: str
    publish_interval_sec: float
    publish_batch_size: int
    publish_retry_delay_sec: float
    task_delay_sec: float
    sqs_region: str
    sqs_queue_url: str


def load_settings() -> Settings:
    database_url = _env("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required")
    return Settings(
        database_url=database_url,
        api_crash_before_outbox=_flag("API_CRASH_BEFORE_OUTBOX"),
        publisher_crash_after_send=_flag("PUBLISHER_CRASH_AFTER_SEND"),
        legacy_inline_publish=_flag("LEGACY_INLINE_PUBLISH"),
        api_crash_after_commit=_flag("API_CRASH_AFTER_COMMIT"),
        broker_kind=_env("BROKER_KIND", "redis"),
        redis_url=_env("REDIS_URL", "redis://redis:6379/0"),
        celery_queue=_env("CELERY_QUEUE", "jobs"),
        publish_interval_sec=float(_env("PUBLISH_INTERVAL_SEC", "1")),
        publish_batch_size=int(_env("PUBLISH_BATCH_SIZE", "10")),
        publish_retry_delay_sec=float(_env("PUBLISH_RETRY_DELAY_SEC", "3")),
        task_delay_sec=float(_env("TASK_DELAY_SEC", "1")),
        sqs_region=_env("SQS_REGION"),
        sqs_queue_url=_env("SQS_QUEUE_URL"),
    )


settings = load_settings()
