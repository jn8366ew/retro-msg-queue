"""로그 한 줄 형식. §12: phase job_id event_id execution_id task_id attempt 를 키=값으로 항상 포함한다."""

import logging

_KEYS = ("phase", "job_id", "event_id", "execution_id", "task_id", "attempt")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def kv(phase: str, **fields: object) -> str:
    """kv("accepted", job_id=1) -> 'phase=accepted job_id=1 event_id=- execution_id=- task_id=- attempt=-'

    고정 키는 항상 순서대로 나오고 없으면 '-'. 추가 키는 뒤에 붙는다.
    """
    fields["phase"] = phase
    head = " ".join(f"{k}={fields.pop(k, '-')}" for k in _KEYS)
    tail = " ".join(f"{k}={v}" for k, v in fields.items())
    return f"{head} {tail}".rstrip()
