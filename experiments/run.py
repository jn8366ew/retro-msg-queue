"""실험 CLI. 컨테이너 안에서 실행: docker compose exec api python experiments/run.py <cmd> ...

1단계: create | get | count
2~3단계에서 wait | republish | backlog 추가 (dev-plan §11).
"""

import argparse
import json
import os
import sys

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

    n = sub.add_parser("count", help="request_key 기준 jobs·outbox_events 행 수")
    n.add_argument("--request-key", required=True)
    n.set_defaults(fn=cmd_count)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
