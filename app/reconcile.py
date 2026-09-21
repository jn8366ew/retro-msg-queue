"""`SENT + PENDING` 분류와 수동 복구 (dev-plan R21·R22, 7단계).

`outbox.SENT + jobs.PENDING`은 "보냈는데 안 끝남"이라는 한 가지 모양이지만, 실제로는
워커 미도달·실행 중·실행 중 사망·반복 사망이 섞여 있다. 실행 기록(`job_executions`)으로 가른다.

    not_started  실행 기록 없음                         — 큐에 있거나, 받기 전에 사라졌다
    running      마지막 시작이 stale_after 초 미만 전    — 아직 기다린다
    stalled      마지막 시작이 stale_after 초 이상 전    — reconcile 대상
    gave_up      끝나지 않은 실행이 max_unfinished 이상  — 재발행하지 않는다

DB는 브로커가 메시지를 쥐고 있는지 모른다. 메시지가 사라진 경우(E8 대조군)와 DLQ에
격리된 경우(E9)는 끝나지 않은 실행 수로만 갈린다 — 확정이 아니라 단서다.
"""

from sqlalchemy import text

_CLASSIFIED = """
WITH sp AS (
    SELECT j.id AS job_id, o.id AS event_id
    FROM outbox_events o JOIN jobs j ON j.id = o.job_id
    WHERE o.status = 'SENT' AND j.status = 'PENDING'
), ex AS (
    SELECT sp.job_id, sp.event_id,
           count(e.execution_id) AS executions,
           count(e.execution_id) FILTER (WHERE e.finished_at IS NULL) AS unfinished,
           EXTRACT(EPOCH FROM now() - max(e.started_at)) AS last_start_age_sec
    FROM sp LEFT JOIN job_executions e ON e.job_id = sp.job_id
    GROUP BY sp.job_id, sp.event_id
)
SELECT job_id, event_id, executions, unfinished, last_start_age_sec,
       CASE
           WHEN unfinished >= :max_unfinished THEN 'gave_up'
           WHEN executions = 0 THEN 'not_started'
           WHEN last_start_age_sec < :stale_after THEN 'running'
           ELSE 'stalled'
       END AS state
FROM ex
"""

STATES = ("not_started", "running", "stalled", "gave_up")


def classify(conn, stale_after: float, max_unfinished: int) -> list[dict]:
    """`SENT + PENDING` 업무마다 한 행. 읽기 전용."""
    rows = conn.execute(
        text(_CLASSIFIED + " ORDER BY job_id"),
        {"stale_after": stale_after, "max_unfinished": max_unfinished},
    ).mappings()
    return [dict(r) for r in rows]


def reconcile(conn, stale_after: float, max_unfinished: int) -> list[int]:
    """`stalled`인 업무의 아웃박스를 PENDING으로 되돌린다. 되돌린 job_id를 반환한다.

    분류와 되돌리기를 한 문장으로 한다. 워커가 그 사이에 끝내도 재발행된 메시지는
    `already_done`으로 끝난다 — 중복 실행은 조건부 UPDATE가 막는다 (E5·E6b).
    `gave_up`은 건드리지 않는다. 상한이 없으면 E9에서 DLQ가 끊은 반복을 여기서 다시 만든다.
    """
    rows = conn.execute(
        text(
            "WITH cls AS (" + _CLASSIFIED + ") "
            "UPDATE outbox_events o "
            "SET status = 'PENDING', next_attempt_at = now(), published_at = NULL, "
            "    last_error = 'reconciled: stalled' "
            "FROM cls WHERE cls.event_id = o.id AND cls.state = 'stalled' AND o.status = 'SENT' "
            "RETURNING o.job_id"
        ),
        {"stale_after": stale_after, "max_unfinished": max_unfinished},
    ).scalars()
    return sorted(rows)
