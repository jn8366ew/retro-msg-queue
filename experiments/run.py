"""실험 CLI. 컨테이너 안에서 실행: docker compose exec api python experiments/run.py <cmd> ...

create | get | wait | count | republish | backlog (dev-plan §11).
"""

import argparse
import json
import os
import sys
import time

import httpx

API_BASE_URL = os.environ.get("API_BASE_URL", "http://api:8000")


def _print_response(r: httpx.Response) -> None:
    print(f"HTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), ensure_ascii=False, indent=2))
    except ValueError:
        print(r.text)


def cmd_create(a: argparse.Namespace) -> int:
    try:
        r = httpx.post(
            f"{API_BASE_URL}/jobs",
            json={"request_key": a.request_key, "value": a.value},
            timeout=10,
        )
    except httpx.HTTPError as exc:
        # E0(api 프로세스 종료) 같은 경우 여기로 온다. 응답 없음 자체가 관측이다.
        print(f"NO RESPONSE: {type(exc).__name__}: {exc}")
        return 2
    _print_response(r)
    return 0


def cmd_get(a: argparse.Namespace) -> int:
    r = httpx.get(f"{API_BASE_URL}/jobs/{a.job_id}", timeout=10)
    _print_response(r)
    return 0


def cmd_wait(a: argparse.Namespace) -> int:
    """jobs.DONE·outbox.SENT까지 폴링하고 상태가 바뀐 시각을 출력한다.

    둘은 별개 상태다. 하나만 도달해도 타임아웃까지 나머지를 기다린다.
    """
    start = time.monotonic()
    seen: dict[str, str] = {}
    job_status = outbox_status = None
    while time.monotonic() - start < a.timeout:
        r = httpx.get(f"{API_BASE_URL}/jobs/{a.job_id}", timeout=10)
        if r.status_code != 200:
            _print_response(r)
            return 1
        d = r.json()
        job_status = d["status"]
        outbox_status = d["outbox"]["status"] if d["outbox"] else "NONE"
        for label, value in (("jobs", job_status), ("outbox", outbox_status)):
            if seen.get(label) != value:
                seen[label] = value
                print(f"[{time.monotonic() - start:6.2f}s] {label}={value}")
        if job_status == "DONE" and outbox_status in ("SENT", "NONE"):
            print(json.dumps(d, ensure_ascii=False, indent=2))
            return 0
        time.sleep(0.2)
    print(f"TIMEOUT after {a.timeout}s: jobs={job_status} outbox={outbox_status}")
    return 1


def cmd_count(a: argparse.Namespace) -> int:
    """jobs·outbox_events 행 수 (E2·E7 확인용). DB 직접 조회."""
    from sqlalchemy import text

    from app.db import engine

    with engine.connect() as conn:
        jobs = conn.scalar(text("SELECT count(*) FROM jobs WHERE request_key = :k"), {"k": a.request_key})
        outbox = conn.scalar(
            text(
                "SELECT count(*) FROM outbox_events o JOIN jobs j ON j.id = o.job_id "
                "WHERE j.request_key = :k"
            ),
            {"k": a.request_key},
        )
    print(json.dumps({"request_key": a.request_key, "jobs": jobs, "outbox_events": outbox}, ensure_ascii=False))
    return 0


def cmd_republish(a: argparse.Namespace) -> int:
    """발행자와 같은 전송 함수로 apply_async만 다시 호출한다. outbox 상태는 건드리지 않는다.

    브로커 재전달(visibility timeout)이나 Celery retry와 구분되는 '수동 재실행'이다
    (면접 정리 7.5절).
    """
    from sqlalchemy import text

    from app.db import engine
    from app.tasks import send_compute

    with engine.connect() as conn:
        job_id = conn.scalar(
            text("SELECT job_id FROM outbox_events WHERE id = :id"), {"id": a.event_id}
        )
    if job_id is None:
        print(f"event_id={a.event_id} not found")
        return 1
    task_id = send_compute(job_id, a.event_id)
    print(json.dumps({"event_id": a.event_id, "job_id": job_id, "task_id": task_id}, ensure_ascii=False))
    return 0


def cmd_backlog(a: argparse.Namespace) -> int:
    """적체 관측. 읽기 전용.

    면접 정리 Q4 꼬리질문("발행자가 죽으면? → 적체 감지 필요")과 Q18("미전송 건수·최장
    대기 시간·미완료 작업 관측")에 대응한다. 과거 프로젝트에 없던 탐지 근거가 이것이다.

    sent_but_pending은 워커 미도달과 실행 중 중단을 구분하지 못한다 (dev-plan §13-3).
    """
    from sqlalchemy import text

    from app.db import engine

    with engine.connect() as conn:
        pending = conn.scalar(text("SELECT count(*) FROM outbox_events WHERE status='PENDING'"))
        oldest = conn.scalar(
            text(
                "SELECT COALESCE(EXTRACT(EPOCH FROM now() - min(created_at)), 0) "
                "FROM outbox_events WHERE status='PENDING'"
            )
        )
        sent_but_pending = conn.scalar(
            text(
                "SELECT count(*) FROM outbox_events o JOIN jobs j ON j.id = o.job_id "
                "WHERE o.status='SENT' AND j.status='PENDING'"
            )
        )
        stuck = conn.scalar(
            text("SELECT count(*) FROM outbox_events WHERE attempts >= :n"),
            {"n": a.min_attempts},
        )
        # 아웃박스 행이 아예 없는 업무 — E0 대조군이 남기는 상태
        no_outbox = conn.scalar(
            text(
                "SELECT count(*) FROM jobs j LEFT JOIN outbox_events o ON o.job_id = j.id "
                "WHERE o.id IS NULL"
            )
        )
    print(
        json.dumps(
            {
                "outbox_pending": pending,
                "oldest_pending_sec": round(float(oldest), 1),
                "sent_but_job_pending": sent_but_pending,
                f"attempts_ge_{a.min_attempts}": stuck,
                "jobs_without_outbox": no_outbox,
            },
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="run.py")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="POST /jobs")
    c.add_argument("--request-key", required=True)
    c.add_argument("--value", type=int, required=True)
    c.set_defaults(fn=cmd_create)

    g = sub.add_parser("get", help="GET /jobs/{job_id}")
    g.add_argument("--job-id", type=int, required=True)
    g.set_defaults(fn=cmd_get)

    w = sub.add_parser("wait", help="DONE·SENT까지 폴링")
    w.add_argument("--job-id", type=int, required=True)
    w.add_argument("--timeout", type=float, default=30)
    w.set_defaults(fn=cmd_wait)

    n = sub.add_parser("count", help="request_key 기준 jobs·outbox_events 행 수")
    n.add_argument("--request-key", required=True)
    n.set_defaults(fn=cmd_count)

    rp = sub.add_parser("republish", help="같은 이벤트를 수동 재발행 (outbox 상태 불변)")
    rp.add_argument("--event-id", type=int, required=True)
    rp.set_defaults(fn=cmd_republish)

    bl = sub.add_parser("backlog", help="적체 관측 (읽기 전용)")
    bl.add_argument("--min-attempts", type=int, default=3)
    bl.set_defaults(fn=cmd_backlog)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
