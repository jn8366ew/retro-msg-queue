# 메시지큐 학습 MVP 개발 기획서 v3 — 아웃박스·Celery·Redis → SQS

- 작성일: 2026-09-17
- 갱신일: 2026-09-20 (v3 — 면접 정리 문서 대조·라이브러리 실동작 검증 반영. v2는 같은 날 구현 착수용 확정본)
- 상태: **1~7단계 완료 (2026-09-22).** Redis 실험 9개 + SQS 재실행 3개(E1·E3·E5) + SQS 전용 4개(E8 재전달·E9 DLQ·E10 `SENT + PENDING` 분류·E11 복구와 상한)를 `reports/`에 기록. E10·E11은 사용자가 `practice/step7.md`로 직접 수행했다 (U8). 실측 결과는 README "결정 기록"과 각 리포트에 있으며, 이 문서의 예상과 다른 값은 해당 절에 표시했다.
- 대조 문서: `insurance_message_queue_interview_notes_2026-09-16.md` — 과거 보험 프로젝트의 실제 구성·미확인 사항·학습한 개선안. 이 MVP가 그중 어느 공백을 겨냥하는지는 §14.
- 진행 상태: `roadmap.md` (단계별 체크리스트). 이 문서는 설계·결정, 로드맵은 진행만 기록한다.
- 실행 순서: **Redis 프로필로 로컬 실험 전부 완료 → 동일 코드로 SQS 프로필 연결**
- 스택: FastAPI + SQLAlchemy 2 (동기) + psycopg 3 + PostgreSQL 16 + Celery 5 + Redis 7 / AWS SQS
- 목적: 재현이 아니라 개선안 실습. 과거 보험 프로젝트의 실제 구성을 복원하지 않는다.

---

## 0. v1 → v2 → v3 갱신 요약

### v1 → v2

v1이 열어둔 결정을 아래처럼 고정했다. 구현은 이 값을 기준으로 하고, 실험 중 값이 바뀌면 README의 "결정 기록"에 남긴다.

| # | 항목 | 확정값 |
|---|---|---|
| 1 | 발행자 "전송 후 기록 전 중단" 지점 | 환경변수 `PUBLISHER_CRASH_AFTER_SEND=1` → apply_async 성공 로그 직후 `os._exit(1)`. 고정 sleep 없음. API에 노출 안 함 |
| 2 | API "jobs 저장 후 outbox 저장 전 예외" 지점 | 환경변수 `API_CRASH_BEFORE_OUTBOX=1` → jobs INSERT flush 후 outbox INSERT 전 `RuntimeError` → 500, 전체 롤백 |
| 3 | 발행 측 Celery 설정 | `task_publish_retry=False`, `broker_connection_timeout=4`. apply_async가 브로커 다운 시 유한 시간 내 예외를 내는지 2단계에서 실측 확인 |
| 4 | 워커 측 Celery 설정 (명시 기록, 중단 복구 실험은 후속) | `task_acks_late=False`, `worker_prefetch_multiplier=1`, `task_reject_on_worker_lost=False`, `broker_connection_retry_on_startup=True`, concurrency=1, prefork |
| 5 | request_key 동시 충돌 처리 | `IntegrityError` → rollback → 재조회 → 입력 동일 200 / 상이 409 |
| 6 | 발행자 조회 | `status='PENDING' AND next_attempt_at <= now() ORDER BY id LIMIT 10`. `FOR UPDATE SKIP LOCKED`는 넣지 않고 주석으로만 후속 표시 |
| 7 | 중복 전달 실험 수단 | `experiments/run.py republish --event-id N` — 발행자와 같은 전송 함수로 apply_async만 재호출. outbox 상태는 건드리지 않음 |
| 8 | SQS 의존성 | `celery[sqs]` (pycurl 포함). Dockerfile에 `libcurl4-openssl-dev libssl-dev gcc` 포함. Redis/SQS 프로필 동일 이미지 |
| 9 | 버전 고정 | Python 3.12. requirements.txt는 `==` 핀. 정확한 버전은 생성 시점 최신 안정판을 조회해 고정하고 README에 기록 |
| 10 | 태스크 계산 | `{"value": v, "square": v*v, "execution_id": ...}`. `TASK_DELAY_SEC`(기본 1초) 지연 |
| 11 | 리포트 한계 명시 | `outbox.SENT + jobs.PENDING`은 "워커 미도달"과 "실행 중 중단"을 구분하지 못한다. 모든 리포트에 이 한계를 항목으로 남긴다 (후속 논의 3번의 동기) |

### v2 → v3 (2026-09-20)

v2를 면접 정리 문서와 대조하고, kombu·celery 소스로 발행 경로를 검증한 결과다. v2 확정값 중 바뀐 것은 이유를 함께 적었다. 여기 없는 v2 값은 그대로다.

**사용자 결정**

| # | 항목 | 결정 |
|---|---|---|
| U1 | 대조군 실험 | **E0 추가.** 아웃박스 없이 "커밋 후 API가 바로 apply_async"하는 과거 방식을 플래그 뒤에 구현하고, 커밋 후·발행 전 중단을 주입한다. 면접 정리 8.7절 "큐 등록 누락은 알림 자체가 없어 탐지 근거가 없었다"를 관측으로 재현한다 |
| U2 | 조건부 UPDATE 실증 | **E6b 추가.** 워커 `--concurrency=2`로 `rejected_already_done`을 관측한다. concurrency=1에서는 §6의 사전 조회가 항상 먼저 걸려 rowcount=0 분기가 어떤 실험에서도 실행되지 않는다. §1 제약(concurrency=1)의 의도적 이탈이며 E6b에서만 |
| U3 | 적체 관측 | **`run.py backlog` 추가.** PENDING 수·최장 대기 초·SENT+jobs.PENDING 수·attempts≥N 수. 면접 정리 Q4·Q18의 "적체 감지·미전송 건수 관측" |
| U4 | acks_late 실험 | **이번 범위 제외.** §13 후속 E8로 기록 |
| U5 | 디렉터리 | 프로젝트 루트는 `retro-msg-queue/` (v2 트리의 `queue-learning/` 대신) |
| U6 | 검증 | 실험 리포트 + **pytest 최소 단위테스트** |
| U7 | 진행 | 1단계부터. 단계별 완료 확인 후 다음 단계 |
| U8 | 7단계 실습 | **E10·E11은 사용자가 직접 수행한다.** 구현과 단위 테스트, 짧은 동작 확인까지는 Claude가 하고, 실험 본편은 `practice/step7.md` 실습 가이드(단계별 명령·보여야 할 출력·스스로 확인할 질문·원복)를 따라 사용자가 돌린다. 리포트는 사용자가 넘긴 출력으로 작성한다 (2026-09-21) |

**기획서 결함·침묵 처리**

| # | 단계 | 결정 | 이유 |
|---|---|---|---|
| R1 | 1 | `init_db()`는 `pg_advisory_xact_lock` + `create_all(conn)`을 한 트랜잭션에 | api·publisher·worker가 동시에 `create_all`을 부르면 경합한다 (`checkfirst`는 has_table→CREATE라 비원자). 세 프로세스 모두 idempotent, 기동 순서 의존 없음 |
| R2 | 1 | postgres `healthcheck: pg_isready -U app -d app` | v2가 `condition: service_healthy`를 요구하면서 healthcheck를 정의하지 않았다 |
| R3 | 1 | `psycopg[binary]` 핀 | 소스 빌드·libpq-dev 회피 |
| R4 | 1 | `pydantic-settings` 대신 `os.environ` + 검증 함수 | 의존성 하나 줄임. 장애 주입 플래그는 요청 시점에 읽는 객체 속성 |
| R5 | 1 | 테스트 DB `app_test`를 postgres initdb 스크립트로 생성. `conftest.py`가 앱 모듈 임포트 전에 `DATABASE_URL`의 DB 이름을 `app_test`로 치환 | 실험 DB 오염 방지. 앱 코드에 테스트 분기 없음. 긴 `-e` 플래그를 없애 PowerShell 붙여넣기 사고와 오입력 여지를 제거 |
| R6 | 1 | `POST /jobs` 202 본문은 v2대로 `{job_id, status}`. 중복 200은 GET 본문 | 통일 제안이 있었으나 스펙을 바꿀 이유가 부족. `run.py`는 status code와 본문을 그대로 출력 |
| R7 | 1 | `.gitattributes` `* text=auto eol=lf`, `PYTHONUNBUFFERED=1`, compose `command:`는 exec 배열 | Windows CRLF, 컨테이너 로그 버퍼링, PID 1이 파이썬이어야 SIGTERM 핸들러가 동작 |
| R8 | 1 | git 저장소 초기화. 첫 커밋은 두 md 문서, 이후 `step N:` | §12 |
| R9 | 2 | 브로커 다운 시 `apply_async`를 **먼저 실측**하고, 그 뒤 `send_compute`가 `app.connection_for_write(transport_options={"max_retries": 0})` 전용 연결로 `apply_async(connection=...)` | 소스 분석: `task_publish_retry=False`는 publish 래퍼만 끈다. 실제 루프는 `Connection.default_channel → _ensure_connection → retry_over_time`이고 `broker_connection_timeout`(=connect_timeout, 기본 4)이 총 예산이다. `broker_connection_retry*`·`broker_connection_max_retries`는 워커 전용. **실측(2026-09-20): 예외 클래스는 예상대로 `kombu.exceptions.OperationalError`, 시간은 예상 ≈6초가 아니라 ≈10초.** `docker compose stop`이 DNS 항목을 지워 이름 해석 실패가 되고 그 자체가 ≈3.9초 걸리기 때문이다. `max_retries: 0` 적용 후 3.9초(예상했던 100ms 미만은 연결 거부 상황에서만 가능). 미조치 시 E4의 v2 관측 창("3~10초")은 실패했을 것이다. 전용 연결이라 워커 재접속 정책엔 손대지 않는다. 상세는 README 결정 기록 |
| R10 | 2 | `worker`의 `depends_on: redis` 제거 | profile 없는 서비스가 profile `redis`의 서비스에 의존하면 프로필 미활성 시 `up` 실패. "프로필 redis에서만 의존"은 단일 서비스 정의로 표현 불가. `broker_connection_retry_on_startup=True`가 그 역할 |
| R11 | 2 | 워커 `@app.task(bind=True, ...)`, `def compute(self, ...)`, `completed_at=func.now()` | v2 §6 의사코드는 `self.request.id`를 쓰면서 bind가 없고 `now()`의 시각 소스가 모호했다 |
| R12 | 3 | E0: `LEGACY_INLINE_PUBLISH=1`이면 outbox 없이 `jobs INSERT → COMMIT → [API_CRASH_AFTER_COMMIT=1: os._exit(1)] → send_compute`. GET의 `outbox`는 `null` | 대조군. 중단 방식은 v2 #1과 같은 `os._exit` |
| R13 | 4 | E6b: 워커 `--concurrency=2`, `TASK_DELAY_SEC=3`, 발행자 중지 상태에서 `republish` 2회 연속 | U2 |
| R14 | 5 | `SQS_QUEUE_NAME` 환경변수 제거. `predefined_queues`의 키는 `CELERY_QUEUE` | kombu SQS transport는 Celery 큐 이름으로 `predefined_queues`를 조회한다 — 불일치 시 `UndefinedQueueException` |
| R15 | 5 | `celery[sqs]`의 pycurl 빌드 의존성(v2 #8) 유지 | 5.5의 urllib3 전환이 5.6.0에서 되돌려졌다(처리량 회귀). kombu 5.6 `requirements/extras/sqs.txt`에 pycurl 있음 |
| R16 | — | `--profile sqs`는 매칭 서비스가 없어 no-op. 기동 명령은 `BROKER_KIND=redis` + `--profile redis` / `BROKER_KIND=sqs` + 프로필 없음 | `BROKER_KIND`(env)와 profile(compose)이 독립이라 어긋나도 아무도 막지 않는다. README에 명시 |
| R17 | 6 | §13-2·§13-7을 **E8(브로커 재전달)·E9(독약 메시지와 DLQ)** 로 승격. **SQS 전용** — Redis 대조는 하지 않는다 (사용자 결정 2026-09-21) | `acks_late=False`에서는 재전달 경로가 닫혀 있어 1~5단계 실험에서 SQS/Redis 차이가 한 번도 관측되지 않았다. DLQ는 Redis transport에 대응물이 없다 |
| R18 | 6 | `TASK_ACKS_LATE`·`TASK_REJECT_ON_WORKER_LOST` env 분기, 기본 0(기존 동작 불변). E8·E9에서만 셸 env로 1 | §13-7 스케치대로 같은 코드로 양쪽을 본다. 기본값 불변이라 기존 실험 재현성이 유지된다 |
| R19 | 6 | 독약 메시지는 `TASK_CRASH_BEFORE_ADOPT=1` — 워커 자식이 계산 후 채택 직전 `os._exit(1)`. 재전달은 `task_reject_on_worker_lost=True`가 만든다. DLQ는 `jobs-dlq`, maxReceiveCount=3 (사용자 결정) | 컨테이너 안에서 자식이 PID 1에 보내는 SIGKILL은 커널이 무시하므로 컨테이너 전체 자살은 불가능하다. 자식만 죽이면 부모가 WorkerLostError를 받고, reject_on_worker_lost=True일 때만 미ack로 되돌린다(=§13-2가 물으려던 관계). 3회 = 최초 1 + 재전달 2 |
| R20 | 7 | §13-3을 **7단계**로 승격. 실행 기록 테이블 `job_executions` 신설 (§4). 시작 기록은 계산 전에 **별도 트랜잭션으로 커밋**하고, 종료 기록(`finished_at`·`outcome`)은 채택과 **같은 트랜잭션**에 쓴다 (사용자 결정 2026-09-21) | 1~6단계의 모든 리포트가 `SENT + PENDING`을 구분하지 못한다는 한계로 끝났다. DB에 실행 시작의 흔적이 없기 때문이다. 시작 기록을 채택과 묶으면 죽었을 때 흔적도 함께 사라지므로 따로 커밋한다. `jobs`에 컬럼을 붙이지 않고 새 테이블로 둔 것은 `init_db`의 `create_all`이 없는 테이블만 만들고 기존 테이블에 컬럼을 추가하지 않기 때문이고, 실행 이력(E5 중복, E9 3회 사망)이 행으로 남기 때문이다 |
| R21 | 7 | `SENT + PENDING` 업무를 4분류한다: `not_started`(실행 기록 없음) / `running`(가장 최근 실행이 미완료이고 시작 후 T초 미만) / `stalled`(T초 이상) / `gave_up`(미완료 실행 누적 3건 이상). T = `STALE_AFTER_SEC`, 기본 **60초** (사용자 결정) | T는 visibility timeout과 같은 딜레마다 — 짧으면 느린 정상 작업을 죽었다고 오판하고, 길면 복구가 늦다. 큐 VT(30초)보다 길게 둬서 `acks_late=True`일 때 **브로커 재전달이 먼저** 시도되고 DB 복구는 그게 실패했을 때만 나서게 한다. **DB만으로 E8 대조군(메시지 소실)과 E9(DLQ 격리)는 구분되지 않는다** — 브로커가 메시지를 쥐고 있는지 DB는 모른다. 미완료 실행 수가 단서일 뿐이다 |
| R22 | 7 | 복구는 **수동 명령 `run.py reconcile`**. `stalled` 업무의 아웃박스 행을 조건부 UPDATE 한 문장으로 `PENDING`에 되돌려 발행자가 다시 보내게 한다(같은 행 재사용 — §13-8의 UNIQUE 유지). `gave_up`은 재발행하지 않고 세기만 한다. 발행자 루프에 넣는 자동 복구는 하지 않는다 (사용자 결정) | 재발행으로 생기는 중복 실행은 조건부 UPDATE가 막는다(E5·E6b에서 검증). 상한 3은 DLQ maxReceiveCount와 대칭이다 — 상한이 없으면 E9에서 DLQ가 끊은 반복을 DB 복구가 다시 만든다. 수동이어야 실험에서 복구 시점을 통제할 수 있다. 워커가 막 끝내는 순간과 경합해도 재발행된 메시지는 `already_done`으로 끝난다 |

---

## 1. 결론 → 제약 → 대안 → 대조

### 결론

**업무 저장과 발행 요청을 같은 DB 트랜잭션에 기록하고, 별도 발행자가 Celery 작업을 큐에 전달하는 MVP**를 만든다.

첫 버전에서 확인할 것은 세 가지다.

1. DB 저장 후 프로세스가 멈추거나 브로커에 연결하지 못해도 발행 요청이 남는가?
2. 발행자를 다시 실행하면 미발행 요청을 찾아 작업을 전달하는가?
3. 같은 요청이 두 번 전달돼도 최종 DB 결과가 한 번만 반영되는가?

### 제약

- 아웃박스의 핵심인 DB 트랜잭션 경계는 직접 구현한다. 프레임워크가 대신 제공하는 아웃박스를 쓰지 않는다.
- Celery 작업을 보내는 것(outbox.SENT)과 작업이 완료되는 것(jobs.DONE)은 다른 상태다.
- SQS 자원·계정·IAM은 사용자가 준비한다. 애플리케이션은 AWS 자원을 생성하지 않는다.
- 첫 버전은 단일 호스트, 발행자 1개, 워커 1개(concurrency=1)로 제한한다.
- SQS와 Redis의 장애·재전달 동작이 같다고 가정하지 않는다. Redis 실험으로 SQS 검증을 대신하지 않는다.

### 대안과 대조

| 선택지 | 비교 | 결정 |
|---|---|---|
| DB 저장 후 API가 바로 apply_async | 코드가 적지만 커밋 후 발행 전 중단 시 재전송 근거가 없음 | **E0 대조군.** `LEGACY_INLINE_PUBLISH=1` 뒤에만 구현. 정상 경로 아님 |
| FastAPI BackgroundTasks에서 발행 | 응답 이후로 실행을 옮겨도 영속적인 발행 요청 기록이 없음 | 사용하지 않음 |
| DB 아웃박스 + 독립 발행자 | 테이블·프로세스가 추가되지만 미발행 요청을 조회해 재전송 가능 | **MVP 중심** |
| 커밋 직후 즉시 발행 + 주기 복구 | 지연은 줄지만 두 경로의 경합 제어가 필요 | 후속. 첫 버전은 주기 발행 경로 하나만 |

아웃박스는 전송 누락 복구를 위한 기록과 절차를 제공한다. 발행이 중복될 수 있으므로 소비자의 결과 반영은 별도로 제어한다.

과거 보험 구조와의 대조: 당시에는 "커밋 후 큐 등록 누락"이 발생해도 PDF 실행 오류 알림 자체가 없어 탐지 근거가 없었다. 이 MVP는 그 지점을 `outbox_events.PENDING` 행으로 남기는 것이 개선의 본질이다. E0이 "탐지 근거 없음"을, E3가 "PENDING 행이 남음"을 같은 조건에서 보여준다.

---

## 2. 범위

### 만드는 것

- `POST /jobs`, `GET /jobs/{job_id}`.
- `jobs`, `outbox_events` 테이블.
- 같은 트랜잭션으로 업무와 발행 요청 저장.
- 미발행 이벤트를 주기 조회하는 독립 발행자 프로세스 1개.
- Celery 태스크 1개와 조건부 DB 결과 반영.
- `BROKER_KIND=redis|sqs` 설정 분기.
- 로컬 전용 장애 주입 2곳 (§0 v2 #1, #2) + E0 대조군 플래그 2개 (`LEGACY_INLINE_PUBLISH`, `API_CRASH_AFTER_COMMIT`).
- 실험 CLI(`experiments/run.py`, `backlog` 포함)와 리포트 파일.
- pytest 최소 단위테스트 (API 계약·중복 접수·롤백·조건부 UPDATE 채택).

### 제외하는 것

실제 보험 도메인, PDF, S3, Slack, 로그인, 프런트엔드, 큐·워커 분리, 자동 미완료 복구, DLQ 운영, CDC, 다중 발행자, 선점 토큰·lease·문서 버전, 즉시 발행 경로, Celery result backend, Celery retry, 관리 UI, 알림, `acks_late` 변경·워커 강제 종료 실험(E8, §13).

**DB 결과 저장만 실험한다.** 외부 부작용(결제·파일·메일)의 중복까지 해결했다고 주장하지 않는다.

---

## 3. 구성과 책임

```text
POST /jobs
  → DB 트랜잭션
      jobs INSERT
      [API_CRASH_BEFORE_OUTBOX=1 이면 여기서 RuntimeError]
      outbox_events INSERT
  → COMMIT
  → 202 + job_id

POST /jobs — E0 대조군 (LEGACY_INLINE_PUBLISH=1, 정상 경로 아님)
  → DB 트랜잭션: jobs INSERT → COMMIT        (outbox 없음)
  → [API_CRASH_AFTER_COMMIT=1 이면 여기서 os._exit(1)]
  → apply_async(job_id, event_id=None)
  → 202 + job_id
  중단 시 남는 것: jobs.PENDING 행 하나. 발행 의도 기록·오류 로그·재시도 근거 없음

publisher (독립 루프, Celery beat 아님)
  → 미발행 이벤트 조회 (PENDING, next_attempt_at 경과)
  → attempts+1, next_attempt_at 갱신 → COMMIT
  → [트랜잭션 밖] apply_async(job_id, event_id)
  → [PUBLISHER_CRASH_AFTER_SEND=1 이면 여기서 os._exit(1)]
  → SENT, published_at 기록 → COMMIT

worker (concurrency=1, prefork)
  → jobs 조회, 이미 DONE이면 기존 결과 반환
  → 계산 (+ TASK_DELAY_SEC)
  → UPDATE jobs ... WHERE id=? AND status='PENDING'
  → rowcount 1이면 채택, 0이면 기존 결과 반환

GET /jobs/{job_id}
  → jobs + outbox_events 조인 조회
```

| 구성 | 역할 | 실행 명령 |
|---|---|---|
| api | 요청 검증, 업무·발행 요청 저장, 상태 조회 | `uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| publisher | 미발행 조회, apply_async, 발행 결과 기록 | `python -m app.publisher` |
| worker | 태스크 실행, 조건부 결과 반영 | `celery -A app.celery_app worker -Q jobs --concurrency=1 --pool=prefork -l info` |
| postgres | 업무·아웃박스 저장 | named volume `pgdata` |
| redis | 브로커 전용 (redis 프로필) | 결과 저장소·업무 DB로 사용 안 함 |

- 세 프로세스는 같은 이미지·같은 코드, 실행 명령만 다르다.
- ORM은 동기 SQLAlchemy 세션. 요청·발행 배치·태스크 실행 단위로 세션을 생성·종료한다.
- FastAPI 핸들러는 `def`(동기)로 작성해 스레드풀에서 실행한다. 이벤트 루프에서 블로킹 DB 호출을 직접 하지 않는다.
- DB 오류 시 롤백 후 단계별 재시도 또는 실패 응답. 트랜잭션은 요청·태스크 단위로 짧게 유지한다.
- 시작 시 DB 준비 확인과 테이블 초기화. 세 프로세스 모두 `init_db()`를 부르되 `pg_advisory_xact_lock` + `create_all`을 한 트랜잭션으로 직렬화한다 (R1). Alembic은 쓰지 않는다.
- 발행자는 Celery 태스크·beat로 스케줄링하지 않는다. 브로커가 죽어도 DB 조회를 계속하는 독립 루프다.
- Celery result backend 없음. `task_ignore_result=True`. 상태는 공통 DB에서만 조회한다.
- 비밀번호·AWS 키는 코드·로그·리포트에 남기지 않는다. `.env.example`만 커밋한다.

---

## 4. 데이터 설계

PostgreSQL 기본 격리 수준(Read Committed)을 전제로 한다. 조건부 UPDATE의 경합 시 조건 재평가는 이 수준의 동작에 의존한다.

### jobs

| 필드 | 타입 | 제약 | 의미 |
|---|---|---|---|
| id | BIGSERIAL | PK | 업무 식별자 (응답의 job_id) |
| request_key | TEXT | UNIQUE NOT NULL | 동일 HTTP 요청 식별 키 |
| input | JSONB | NOT NULL | 변경하지 않는 입력. `{"value": int}` |
| status | TEXT | NOT NULL, CHECK IN ('PENDING','DONE') | 결과 반영 여부 |
| result | JSONB | NULL | 채택된 결과. `execution_id` 포함 |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now() | 접수 시각 |
| completed_at | TIMESTAMPTZ | NULL | 결과 채택 시각 |

### outbox_events

| 필드 | 타입 | 제약 | 의미 |
|---|---|---|---|
| id | BIGSERIAL | PK | 이벤트 식별자 (event_id) |
| job_id | BIGINT | FK jobs(id), UNIQUE NOT NULL | 업무당 이벤트 하나 |
| event_type | TEXT | NOT NULL | 고정값 `'COMPUTE_JOB'` |
| status | TEXT | NOT NULL, CHECK IN ('PENDING','SENT') | 발행 성공 기록 여부 |
| attempts | INT | NOT NULL DEFAULT 0 | 발행 시도 기록 (호출 전 중단 포함) |
| next_attempt_at | TIMESTAMPTZ | NOT NULL DEFAULT now() | 다음 시도 대상 시각 |
| last_error | TEXT | NULL | 최근 발행 오류 (500자 절단) |
| created_at | TIMESTAMPTZ | NOT NULL DEFAULT now() | 생성 시각 |
| published_at | TIMESTAMPTZ | NULL | SENT 기록 시각 |

인덱스: `(status, next_attempt_at)`.

메시지 인자는 `job_id`, `event_id`만 보낸다. 입력은 DB에서 읽는다. 메시지에서 태스크 이름을 받아 실행하지 않는다 — 태스크는 `jobs.compute` 하나로 고정.

### job_executions (7단계, R20)

| 컬럼 | 타입 | 제약 | 쓰는 시점 |
|---|---|---|---|
| `execution_id` | uuid | PK | 태스크 시작. 워커가 이미 로그에 찍는 실행별 UUID |
| `job_id` | bigint | FK → jobs.id, 인덱스 | 태스크 시작 |
| `task_id` | text | NOT NULL | 태스크 시작. 브로커 재전달이면 이전 실행과 같다 |
| `started_at` | timestamptz | NOT NULL, `now()` | 태스크 시작 — **계산 전에 별도 트랜잭션으로 커밋** |
| `finished_at` | timestamptz | NULL | 태스크 종료 — 채택과 같은 트랜잭션 |
| `outcome` | text | NULL, `IN ('adopted','rejected','already_done')` | 태스크 종료. **죽으면 NULL로 남는다** |

`finished_at IS NULL`인 행이 "시작했지만 끝을 기록하지 못한 실행"이다. 실행 중이거나 죽은 것이며, 둘은 `started_at`의 나이로만 가른다 (R21).

### 상태 의미

- `jobs.PENDING`: 결과 미반영. 실행 전·실행 중·중단을 이 값 하나로 구별하지 않는다.
- `jobs.DONE`: 결과가 DB에 반영됨.
- `outbox.PENDING`: 발행 성공 기록이 없음. 실제로 전송된 적이 없다는 뜻은 아니다.
- `outbox.SENT`: 발행 성공을 기록함. 작업 완료를 뜻하지 않는다.

이론상 `jobs.DONE`이 `outbox.SENT`보다 먼저 보일 수 있고 그것은 정상이다. 다만 E1에서는 관측되지 않는다 — 발행자는 apply_async 반환 직후(ms)에 SENT를 기록하고 워커는 `TASK_DELAY_SEC`(1초) 뒤에 DONE을 쓴다. DONE-before-SENT는 SENT가 아예 기록되지 않는 E5에서만 보인다.

`attempts`는 apply_async 호출 전에 증가시키므로 실제 브로커 수신 횟수와 같지 않다. 각 시도에 `attempt` 번호와 Celery `task_id`를 로그로 남겨 대조한다.

---

## 5. API 계약

### POST /jobs

요청:

```json
{ "request_key": "study-001", "value": 7 }
```

- `request_key`: 1~100자 문자열. `value`: 정수.

처리:

1. Pydantic으로 검증. 실패 시 422.
2. 한 트랜잭션에서 `jobs` INSERT → (장애 주입 지점) → `outbox_events` INSERT → COMMIT.
3. 성공 시 `202 {"job_id": 1, "status": "PENDING"}`.
4. `request_key` 유니크 충돌(`IntegrityError`) 시 롤백 후 기존 행 재조회:
   - 저장된 `input`과 요청 입력이 같으면 `200` + GET과 같은 본문.
   - 다르면 `409 {"detail": "request_key already used with different input"}`.
   - 사전 SELECT로 중복을 거르지 않는다. 충돌은 제약으로 잡고 사후 처리한다.
5. `API_CRASH_BEFORE_OUTBOX=1`이면 outbox INSERT 전 `RuntimeError` → 500. 두 테이블 모두 롤백.

API는 브로커에 연결하지 않는다. 브로커가 죽어도 DB 접수가 성공하면 202다.

### GET /jobs/{job_id}

```json
{
  "job_id": 1,
  "status": "DONE",
  "input": { "value": 7 },
  "result": { "value": 7, "square": 49, "execution_id": "..." },
  "created_at": "...",
  "completed_at": "...",
  "outbox": {
    "event_id": 1,
    "status": "SENT",
    "attempts": 1,
    "next_attempt_at": "...",
    "last_error": null,
    "published_at": "..."
  }
}
```

- 없는 `job_id`는 404.
- `outbox`는 E0(`LEGACY_INLINE_PUBLISH=1`)으로 접수된 행에서 `null`이다. 정상 경로에서는 항상 있다.
- 두 엔드포인트 외 목록·관리 API는 없다.

---

## 6. 발행자와 워커

### 발행자 (`app/publisher.py`)

```text
시작:
  DB 연결 확인, 설정 로그 (BROKER_KIND, 큐 이름, 간격, 배치 크기)
반복 (PUBLISH_INTERVAL_SEC 마다):
  [tx1] SELECT id, job_id FROM outbox_events
        WHERE status='PENDING' AND next_attempt_at <= now()
        ORDER BY id LIMIT PUBLISH_BATCH_SIZE
        -- 후속: 다중 발행자 시 FOR UPDATE SKIP LOCKED
  각 이벤트:
    [tx2] UPDATE attempts = attempts+1,
                 next_attempt_at = now() + PUBLISH_RETRY_DELAY_SEC
          WHERE id = :event_id  → COMMIT
    [트랜잭션 밖] task_id = send_compute(job_id, event_id)
        실패 → [tx3] UPDATE last_error = :err WHERE id = :event_id → COMMIT, 다음 이벤트
        성공 → log "published event_id=.. job_id=.. attempt=.. task_id=.."
               PUBLISHER_CRASH_AFTER_SEND=1 이면 os._exit(1)
    [tx4] UPDATE status='SENT', published_at=now()
          WHERE id = :event_id AND status='PENDING' → COMMIT
  DB 오류: 로그 후 롤백, 다음 주기로
```

- `send_compute(job_id, event_id)`는 `app/tasks.py`의 함수로 두고, 발행자·`run.py republish`·E0 경로가 공유한다. 내부는 `compute.apply_async(args=[job_id, event_id], queue=CELERY_QUEUE, connection=conn)` 하나. `conn`은 `app.connection_for_write(transport_options={"max_retries": 0})`로 만든 전용 연결이다 — **단, 2단계에서 스펙 설정 그대로 먼저 실측한 뒤 적용한다** (R9). task_id는 Celery 기본(uuid4)에 맡기고 반환값을 로그에 남긴다. 같은 task_id 재사용으로 중복 제거를 기대하지 않는다.
- 발행 실패 예외는 `kombu.exceptions.OperationalError`로 잡는다. redis 드라이버 예외는 kombu가 감싸서 올린다.
- 최대 시도 초과로 이벤트를 삭제하거나 상태를 바꾸지 않는다. 잘못된 자격 증명 같은 영구 오류도 `last_error`에 남아 사용자가 원인을 고치거나 발행자를 멈춘다.
- SIGTERM/SIGINT는 현재 이벤트 처리를 마친 뒤 루프를 빠져나간다. 강제 중단 실험은 `docker compose kill`로 한다.

### 워커 (`app/tasks.py`)

```python
import time
from uuid import uuid4
from sqlalchemy import func, update

@app.task(bind=True, name="jobs.compute")
def compute(self, job_id: int, event_id: int | None) -> dict:
    execution_id = str(uuid4())
    task_id = self.request.id
    log("start", job_id=job_id, event_id=event_id, execution_id=execution_id, task_id=task_id)

    with session_scope() as s:
        job = s.get(Job, job_id)
        if job is None:
            raise ValueError(f"job {job_id} not found")   # 없는 업무를 성공 처리하지 않음
        if job.status == "DONE":                          # 계산 생략 최적화 — 보장이 아니다
            log("already_done", ...); return {"adopted": False, "result": job.result}
        value = job.input["value"]

    time.sleep(TASK_DELAY_SEC)
    result = {"value": value, "square": value * value, "execution_id": execution_id}

    with session_scope() as s:
        rowcount = s.execute(
            update(Job).where(Job.id == job_id, Job.status == "PENDING")
                       .values(status="DONE", result=result, completed_at=func.now())
        ).rowcount
        if rowcount == 1:
            log("adopted", ...); return {"adopted": True, "result": result}
        job = s.get(Job, job_id)
        log("rejected_already_done", ...); return {"adopted": False, "result": job.result}
```

- **보장은 조건부 UPDATE다.** `status == "DONE"` 사전 조회는 계산을 건너뛰는 최적화일 뿐 동시 실행을 막지 못한다 (면접 정리 3절 "사전 조회만으로 동시 실행을 막을 수 없다"). concurrency=1에서는 사전 조회가 항상 먼저 걸리므로 `rejected_already_done`은 E6b(concurrency=2)에서만 관측된다.
- `event_id`는 E0 경로에서 `None`이다. 로그에는 그대로 `event_id=None`으로 남긴다.
- **계산은 반복될 수 있다.** 보장하는 것은 같은 업무의 완료 결과가 한 번 채택되고 덮어써지지 않는 것이다.
- 자동 retry 없음. 예외는 그대로 FAILURE로 끝낸다. 재시도 계층 실험은 후속.
- 로그 한 줄에 `job_id, event_id, execution_id, task_id, phase`를 항상 포함한다.

---

## 7. 확정 설정값

### Celery (`app/celery_app.py`)

| 설정 | 값 | 이유 |
|---|---|---|
| broker_url | redis: `REDIS_URL` / sqs: `sqs://` | BROKER_KIND로 분기 |
| broker_transport_options (redis) | `{"visibility_timeout": 3600}` | 명시 기록. acks_late=False라 실험 대상 아님 |
| broker_transport_options (sqs) | `{"region": SQS_REGION, "predefined_queues": {CELERY_QUEUE: {"url": SQS_QUEUE_URL}}, "polling_interval": 1, "wait_time_seconds": 10}` | 큐 생성 안 함. 자격 증명은 AWS 표준 체인 |
| task_default_queue | `CELERY_QUEUE` (기본 `jobs`) | 워커 `-Q`와 일치 |
| task_serializer / accept_content | json / ["json"] | |
| task_ignore_result | True | result backend 없음 |
| result_backend | None | |
| task_acks_late | False | 기본값 명시. 중단 복구 실험은 후속 |
| task_reject_on_worker_lost | False | 기본값 명시 |
| worker_prefetch_multiplier | 1 | |
| task_publish_retry | False | 발행자 루프가 재시도 담당. publish 래퍼의 재시도만 끈다 — 연결 수립 재시도는 별개 (R9) |
| broker_connection_timeout | 4 | Celery 기본값과 동일. 발행 측에서는 kombu `retry_over_time`의 총 예산으로 작동해 ≈6초 후 예외 (R9) |
| broker_connection_retry_on_startup | True | 워커가 Redis보다 먼저 떠도 재접속. 워커 전용 설정 |
| 발행 전용 연결 `transport_options` | `{"max_retries": 0}` | `send_compute`만. `app.connection_for_write(...)`로 생성. 2단계 실측 후 적용 (R9) |

**2단계 검증 항목 — 실측 완료 (2026-09-20).** 예외 클래스는 예상대로 `kombu.exceptions.OperationalError`였고, **소요 시간은 예상(≈6초)과 달라 ≈10초**였다. `docker compose stop redis`가 컨테이너 DNS 항목을 지워 오류가 "연결 거부"가 아니라 이름 해석 실패가 되고, 그 자체가 ≈3.9초 걸린다 — 시도(3.9) → 대기 2초 → 시도(3.9) = 9.8초가 `broker_connection_timeout=4` 예산을 넘겨 raise. 컨테이너가 없는 경우와 멈춘 경우가 같다. `max_retries: 0` 적용 후 **10초 → 3.9초**이며, 남은 3.9초는 DNS 해석 시간이라 애플리케이션이 줄일 수 없다(예상했던 100ms 미만은 연결 거부 상황에서만 나온다). 전체 수치·메시지는 README 결정 기록과 `reports/E1-20260920-1.md`.

### 환경변수 (`.env.example`)

```text
DATABASE_URL=postgresql+psycopg://app:app@postgres:5432/app
BROKER_KIND=redis                # redis | sqs
REDIS_URL=redis://redis:6379/0
CELERY_QUEUE=jobs
PUBLISH_INTERVAL_SEC=1
PUBLISH_BATCH_SIZE=10
PUBLISH_RETRY_DELAY_SEC=3
TASK_DELAY_SEC=1
PUBLISHER_CRASH_AFTER_SEND=0     # 로컬 실험 전용
API_CRASH_BEFORE_OUTBOX=0        # 로컬 실험 전용
LEGACY_INLINE_PUBLISH=0          # E0 대조군 전용. 1이면 outbox 없이 커밋 후 API가 직접 apply_async
API_CRASH_AFTER_COMMIT=0         # E0 전용. LEGACY_INLINE_PUBLISH=1일 때 커밋 후·발행 전 os._exit(1)
# sqs 프로필. SQS 큐 이름은 CELERY_QUEUE와 같아야 한다 (predefined_queues 키, R14)
SQS_REGION=
SQS_QUEUE_URL=
# AWS 자격 증명은 .env에 넣지 않는다. 프로파일·역할·컨테이너 환경의 표준 체인 사용
```

### Docker Compose

- 서비스: `postgres`(항상), `redis`(profile `redis`), `api`, `publisher`, `worker`.
- 기동: `BROKER_KIND=redis docker compose --profile redis up --build` / `BROKER_KIND=sqs docker compose up --build`. `--profile sqs`는 매칭 서비스가 없어 no-op이다. `BROKER_KIND`와 profile은 서로를 강제하지 않는다 (R16).
- `postgres`는 `pgdata` named volume + `healthcheck: pg_isready -U app -d app` (R2) + initdb 스크립트로 `app_test` DB 생성 (R5). 애플리케이션 컨테이너에 DB 파일을 공유하지 않는다.
- api·publisher·worker는 `depends_on: postgres (condition: service_healthy)`만. **redis에는 의존하지 않는다** (R10) — worker는 `broker_connection_retry_on_startup=True`로, publisher는 자체 루프로 브로커 부재를 견딘다.
- 모든 앱 서비스: `restart: "no"`(E0·E5의 `os._exit`가 무한 재기동되지 않게), `command:`는 exec 배열 형식(`sh -c` 금지 — PID 1이 파이썬이어야 SIGTERM 핸들러가 받는다), `PYTHONUNBUFFERED=1`. 장애 주입 플래그는 `environment: X: ${X:-0}`로 셸 env 오버라이드를 허용한다.
- 장애 실험은 `docker compose stop redis`, `docker compose kill publisher`, 환경변수 변경 후 `docker compose up -d --force-recreate <service>`로 한다.
- 이미지 하나. Dockerfile에 `libcurl4-openssl-dev libssl-dev gcc` 포함 (pycurl, R15). `COPY app/ experiments/ tests/`.

---

## 8. 실험과 완료 기준

모든 실험은 고유 `request_key`와 `run_id`(예: `E3-20260921-1`)를 쓴다. 정상 실험과 장애 실험을 하나씩 실행하고 `job_id · event_id · execution_id · task_id`로 결과를 연결한다.

| # | 실험 | 재현 절차 | 기대 관측 |
|---|---|---|---|
| E0 | **대조군 — 아웃박스 없는 과거 방식** | api를 `LEGACY_INLINE_PUBLISH=1 API_CRASH_AFTER_COMMIT=1`로 재기동 → create → 연결 끊김(api 프로세스 종료) → 플래그 끄고 `compose up -d api` → GET → `run.py backlog` → `redis-cli llen jobs` → 워커 로그 | jobs.PENDING 행만 있고 `outbox: null`. backlog에 잡히지 않음. 브로커 메시지 없음. 워커 로그 없음. **아무도 이 건을 찾을 근거가 없다.** E3와 대비 |
| E1 | 정상 흐름 | `run.py create` → `run.py wait` | jobs.DONE, outbox.SENT, result.execution_id 1개. SENT가 DONE보다 먼저 (§4) |
| E2 | 트랜잭션 롤백 | api를 `API_CRASH_BEFORE_OUTBOX=1`로 재기동 → create → 500 확인 → `run.py count` + psql로 두 테이블 조회 → `redis-cli llen jobs` 전후 | 해당 request_key 행 없음(두 테이블 0), 브로커 메시지 수 불변. 브로커 항목은 API에 브로커 코드 경로가 없어 생기는 **구조적 보장**임을 리포트에 명시. BIGSERIAL 번호 건너뜀은 정상 |
| E3 | 발행자 중단 | `compose stop publisher` → create → GET(PENDING/PENDING) → `run.py backlog`(1건, 대기 초 증가) → `compose start publisher` | 재시작 후 SENT·DONE. E0과 달리 PENDING 행이 탐지 근거 |
| E4 | 브로커 접속 실패 | `compose stop redis` → create → 202 확인 → 5~15초 후 GET (attempts≥2, last_error 있음) → `compose start redis` | 접수 성공, 오류 기록, 복구 후 전달. `max_retries: 0` 적용 후 attempts=2는 t≈4s. 적용 전이라면 ≈6초 블로킹 때문에 t≈8s (R9 실측과 대조) |
| E5 | 발행 후 기록 전 중단 | **사전에 PENDING 잔여 행이 없는지 `backlog`로 확인** → publisher를 `PUBLISHER_CRASH_AFTER_SEND=1`로 재기동 → create → publisher 로그에 published 후 종료 확인 → GET(outbox PENDING, jobs DONE) → 플래그 끄고 재기동 | 같은 event_id가 다시 발행됨. 워커 로그 2건, 첫 실행 `adopted`, 두 번째 `already_done`. **DONE-before-SENT는 여기서만 관측** |
| E6 | 중복 전달 (직렬) | E1 완료 후 `run.py republish --event-id N` 2회 | 워커 로그 `already_done` 2건, result.execution_id 불변. concurrency=1이라 `rejected_already_done`은 나오지 않는다 |
| E6b | **중복 전달 (동시)** | 워커를 `--concurrency=2`, `TASK_DELAY_SEC=3`으로 재기동 → `compose stop publisher` → create → `republish --event-id N` 2회 연속(둘 다 sleep 창 안에 도달) → 워커 로그 → `compose start publisher` | 한쪽 `adopted`, 다른 쪽 `rejected_already_done`(rowcount 0). 발행자 재시작 후 세 번째 실행은 `already_done`. result.execution_id는 adopted 쪽 하나. 조건부 UPDATE가 실제로 동작함을 보는 유일한 실험 (R13) |
| E7 | HTTP 중복 접수 | 같은 request_key·같은 value 재요청, 다른 value 재요청 | 200 동일 job_id / 409, 행 수 불변 (`run.py count`) |
| E8 | **브로커 재전달 (SQS 전용, 6단계)** | `BROKER_KIND=sqs`. ① 대조: 기본 설정(`TASK_ACKS_LATE=0`) + `TASK_DELAY_SEC=20`으로 워커 재기동 → create → 워커 `phase=start` 확인 → `compose kill worker` → 워커 재기동 → 60초 이상 관측. ② 본실험: `TASK_ACKS_LATE=1`로 같은 절차 | ① 메시지는 수신 즉시 삭제됐으므로 재전달 없음 — `SENT + jobs.PENDING` **영구 잔류** (§13-3의 실물). ② visibility timeout(30초) 후 같은 메시지 재수신 → 새 execution_id로 `adopted`, jobs.DONE. 재전달과 멱등성의 협동 |
| E9 | **독약 메시지와 DLQ (SQS 전용, 6단계)** | 사전: `jobs-dlq` 생성 + `jobs`에 redrive(maxReceiveCount=3, 사용자 준비) → `TASK_ACKS_LATE=1 TASK_REJECT_ON_WORKER_LOST=1 TASK_CRASH_BEFORE_ADOPT=1`로 워커 재기동 → create → 워커 로그에서 자식 사망 3회 관측 → `jobs-dlq` 메시지 수 조회 → 플래그 끄고 재기동 | 수신 3회(최초+재전달 2) 동안 매번 `injected_crash` 후 WorkerLostError, 4번째 전달 대신 메시지가 `jobs-dlq`로 이동. **jobs는 PENDING 잔류 — DLQ는 전달 루프를 끊을 뿐 업무를 복구하지 않는다.** 재전달 간격(즉시 0초인지 visibility timeout 30초인지)은 kombu reject 구현에 달렸으므로 실측해 기록한다 |
| E10 | **`SENT + PENDING` 탐지 (7단계)** | 네 상황을 차례로 만든다. ① `docker compose stop worker` 후 create → 발행 확인 → backlog. ② `TASK_DELAY_SEC=20`으로 create → 실행 중 backlog. ③ E8 대조군 재현(`acks_late=0`, 실행 중 kill) → 60초 전후로 backlog 두 번. ④ E9 재현(독약 + DLQ) → backlog | ① `not_started=1` ② `running=1` ③ 60초 전 `running`, 후 `stalled` ④ `gave_up=1`(미완료 실행 3건). 기존 `sent_but_job_pending`은 네 경우 모두 1. **③과 ④가 DB에서 미완료 실행 수로만 갈린다는 것**을 함께 기록한다 |
| E11 | **복구와 상한 (7단계)** | ⓐ E10-③ 상태에서 `run.py reconcile` → 발행자 재발행 → 워커(정상 설정) 완료 관측. ⓑ `TASK_CRASH_BEFORE_ADOPT=1`, `acks_late=0`(DLQ가 개입하지 않는 독약)으로 create → 60초마다 reconcile 반복 → 세 번째 사망 뒤 reconcile이 재발행을 거부하는지 관측. ⓒ E10-④(DLQ 격리) 상태에서 reconcile | ⓐ 아웃박스 `attempts=2`, 실행 기록 2행(첫 행 미완료, 둘째 `adopted`), jobs `DONE`. ⓑ 미완료 실행 1→2→3, 세 번째 뒤 `gave_up`, 재발행 없음, jobs `PENDING` 잔류(약 3~4분). ⓒ 재발행 없음 — DLQ에 이미 격리된 업무를 DB 복구가 다시 살리지 않는다 |

### 리포트 형식 (`reports/<run_id>.md`)

```text
# <run_id> — <실험명>
- 실행 시각, BROKER_KIND, 이미지 태그·패키지 버전 (README 참조)
- 실행한 명령 (순서대로)
- 관측: GET 응답 전후, 관련 로그 라인 (job_id/event_id/execution_id/task_id)
- 결론: 기대 관측과 일치 여부
- 한계: outbox.SENT + jobs.PENDING 상태는 워커 미도달과 실행 중 중단을 구분하지 못함 (모든 리포트에 고정 항목)
```

### MVP 완료 체크리스트

- [x] E0~E7(E6b 포함) Redis 프로필로 실행하고 리포트를 남겼다.
- [x] 아웃박스 없는 과거 방식에서 "탐지 근거 없음"을 관측하고 E3와 대비했다 (E0).
- [x] 업무와 발행 요청의 원자적 저장·롤백을 확인했다 (E2).
- [x] 브로커 없이도 DB 접수 기록을 남겼다 (E4).
- [x] 발행자·브로커 복구 후 미발행 요청을 전달했다 (E3, E4).
- [x] SENT가 업무 완료와 다른 상태임을 확인했다 (E5에서 SENT 전 DONE 관측).
- [x] 전송 후 기록 전 중단에서 중복 발행을 재현했다 (E5).
- [x] 중복 실행에도 채택된 DB 결과가 바뀌지 않았다 (E5, E6). 조건부 UPDATE의 rowcount=0 분기를 `rejected_already_done`으로 관측했다 (E6b).
- [x] §7 2단계 검증 항목을 실측하고 결정 기록에 남겼다 (R9).
- [x] pytest가 통과했다.
- [x] README에 실행 방법·패키지 버전·결정 기록을 남겼다.
- [x] SQS는 실제 연결·실험(E1, E3, E5)을 수행했을 때만 체크한다. — 2026-09-21 수행, 리포트 3개.
- [x] E8 — acks_late 0/1 대조로 메시지 소실과 재전달을 관측했다 (SQS). — 2026-09-21, 재전달 간격 30.003초
- [x] E9 — 독약 메시지가 3회 수신 후 DLQ로 이동하고 jobs가 PENDING으로 남는 것을 관측했다 (SQS). — 2026-09-21, 재전달 간격 10초(`wait_time_seconds`)
- [ ] E10 — 같은 `SENT + PENDING`을 not_started / running / stalled / gave_up으로 나눠 관측했다.
- [ ] E11 — stalled 업무를 reconcile로 완료시켰고, 독약은 3회에서 재발행이 멈추는 것을 관측했다.

---

## 9. Redis → SQS 전환

### 공통

- 발행 코드, 태스크 이름, 메시지 인자, DB 조회 API 전부 동일. 연결 설정과 transport option만 다르다.
- 별도 메시징 추상화나 이중 발행 경로를 만들지 않는다.

### Redis (1단계 완료 대상)

- 브로커 전용. 장애 실험은 redis 컨테이너만 중지한다.
- E0~E7(E6b 포함) 전부 여기서 완료한다.

### SQS (2단계, 사용자 준비 후)

| 사용자 준비 | 애플리케이션 처리 |
|---|---|
| Standard 큐 생성 | `predefined_queues`로 기존 큐 참조 |
| 리전·큐 URL. 큐 이름은 `CELERY_QUEUE`(기본 `jobs`)와 같아야 한다 | 환경변수 + 시작 시 설정 검증 (빈 값이면 기동 실패, `SQS_QUEUE_URL`이 `/{CELERY_QUEUE}`로 끝나지 않으면 기동 실패 — R14) |
| 자격 증명 (프로파일·역할) | AWS 표준 인증 체인. `.env`에 키 없음 |
| 큐 접근 권한 | 필요한 권한(`sqs:SendMessage`, `ReceiveMessage`, `DeleteMessage`, `GetQueueAttributes`)과 연결 오류 확인 방법을 README에 기록 |
| visibility timeout 등 큐 속성 | 태스크 시간·ACK 설정과 함께 README에 기록 |

- SQS 프로필에서는 redis 컨테이너를 띄우지 않는다.
- 최초 SQS 검증은 E1·E3·E5로 제한한다. visibility timeout 초과·ACK 변경 실험은 후속.
- Celery remote control·events를 SQS 관측 전제로 삼지 않는다. `celery inspect`가 SQS에서 동작하지 않아도 정상이다.
- Redis 실험 결과로 SQS 항목을 완료 처리하지 않는다.

---

## 10. 보장 범위와 남는 한계

| 이 MVP에서 확인할 것 | 보장하지 않는 것 |
|---|---|
| 같은 DB에 업무와 발행 의도를 함께 저장 | DB와 브로커를 하나의 원자적 트랜잭션으로 묶기 |
| 남은 미발행 요청을 다시 보내기 | DB·프로세스·브로커가 영구 중단돼도 자동 완료 |
| 같은 업무의 최종 DB 결과 유지 | 함수 실행이 반드시 한 번뿐임 |
| 발행 상태와 업무 상태 조회 | 워커 중단·알림 누락의 자동 탐지·복구 |
| 로컬 Compose 소규모 실습 | 다중 발행자·분산 배포·고부하 |

- `outbox.SENT` 이후 워커가 실행 도중 중단되거나 브로커가 메시지를 잃으면 `jobs.PENDING`이 남는다. 아웃박스만으로 해결되지 않는다. MVP는 GET과 로그로 확인하고 `run.py republish`로 수동 재발행한다.
- `SENT + PENDING`은 "워커 미도달"과 "실행 중 중단"을 구분하지 못한다. 구분하려면 실행 시작 기록(점유 상태·시각)이 필요하다 — 후속 논의 3번. → **7단계에서 `job_executions`로 해소 (R20~R22, E10).** 남은 것: 메시지 소실과 DLQ 격리는 미완료 실행 수로 추정할 뿐이고, `not_started`에는 나이 기준이 없어 워커에 닿기 전에 사라진 메시지를 `reconcile`이 구하지 못한다.
- PostgreSQL 커밋 기록과 볼륨이 유지되고 DB·발행자·브로커가 복구돼야 재시도가 이어진다. "아웃박스를 쓰면 유실이 없다"로 설명하지 않는다.
- `acks_late=False`이므로 워커가 태스크 실행 중 죽으면 브로커는 메시지를 재전달하지 않는다. 이 설정을 바꾸는 실험은 후속이며, 바꾸면 E5·E6의 관측도 달라진다.

---

## 11. 개발 순서와 파일 구성

| 순서 | 개발 내용 | 완료 확인 |
|---|---|---|
| 1 | Compose(postgres) + models + db + API 2개 + `run.py create/get/count` + pytest | E2, E7 통과. pytest 통과. 워커·발행자 없이 접수·조회·롤백·중복 접수 |
| 2 | redis + celery_app + tasks + publisher + `run.py wait` | E1 통과. §7 "2단계 검증 항목" 실측 → `max_retries: 0` 적용 (R9) |
| 3 | 장애 주입 2곳 + E0 플래그 2곳 + `run.py republish/backlog` | E0, E3, E4 통과 |
| 4 | 중단·중복 실험 | E5, E6, E6b 통과. 리포트 9개 |
| 5 | SQS 프로필 | 사용자 준비 후 E1·E3·E5 |

1~4까지가 로컬 아웃박스 MVP 완료다. 5는 로컬 완료를 막지 않는다. 각 단계는 사용자 확인 후 다음으로 넘어간다 (U7).

```text
retro-msg-queue/
  dev-plan.md                                   # 이 문서 — 설계·결정
  roadmap.md                                    # 단계별 체크리스트 — 진행 상태
  insurance_message_queue_interview_notes_2026-09-16.md
  app/
    __init__.py
    config.py         # os.environ 로드·검증 (R4)
    db.py             # engine, session_scope(), init_db() (advisory lock, R1), wait_for_db()
    models.py         # Job, OutboxEvent
    main.py           # FastAPI, POST/GET /jobs (+ E0 분기)
    celery_app.py     # BROKER_KIND 분기, §7 설정
    tasks.py          # compute 태스크, send_compute()
    publisher.py      # 독립 루프
  experiments/
    run.py            # create | get | wait | republish | count | backlog
  tests/
    conftest.py       # DATABASE_URL이 app_test인지 assert, 테이블 정리 픽스처, TestClient
    test_api.py       # 202·200·409·500 롤백·404
    test_adopt.py     # 조건부 UPDATE 채택 (2단계)
  compose/
    postgres-init/01-test-db.sql   # CREATE DATABASE app_test (R5)
  reports/
    .gitkeep
  compose.yaml
  Dockerfile
  pyproject.toml      # pytest 설정만
  requirements.txt    # == 핀
  .env.example
  .gitattributes      # * text=auto eol=lf (R7)
  .gitignore          # .env, __pycache__, .pytest_cache
  README.md           # 실행 방법, 패키지 버전, 결정 기록, 실험 목록, Django 대응표(§14)
```

`experiments/run.py` 서브커맨드:

- `create --request-key K --value V` → POST 호출, status code와 본문 출력 (202/200/409/500 분기 없이 그대로).
- `get --job-id N` → GET 호출, 응답 출력.
- `wait --job-id N --timeout 30` → DONE·SENT까지 폴링, 상태 변화 시각 출력.
- `republish --event-id N` → DB에서 job_id 조회 후 `send_compute` 직접 호출. outbox 상태 변경 없음.
- `count --request-key K` → jobs·outbox_events 행 수 출력 (E2·E7 확인용).
- `backlog [--min-attempts 3]` → outbox PENDING 수, 최장 대기 초(가장 오래된 PENDING의 `created_at` 기준), SENT이면서 jobs.PENDING인 수, `attempts >= N`인 수. 읽기 전용. E0·E3·E5 관측용 (U3).

`run.py`는 컨테이너 안(`docker compose exec api python experiments/run.py ...`)에서 실행한다. `API_BASE_URL` 기본값 `http://api:8000`. `republish`가 api 컨테이너에서 브로커에 붙는 것은 §5 "API는 브로커에 연결하지 않는다"의 실험용 예외다.

pytest는 `docker compose exec api pytest -q`로 실행한다. `conftest.py`가 앱 모듈 임포트 전에 `DATABASE_URL`의 DB 이름을 `app_test`로 치환하므로 별도 플래그가 필요 없고, 실험 DB(`app`)를 건드릴 경로도 없다.

과도한 계층 분리(repository·service·DTO 분리)를 하지 않는다. 파일 하나에 역할 하나면 충분하다.

---

## 12. Claude Code 실행 지침

이 절은 Claude Code가 이 문서를 받아 구현할 때의 규칙이다.

### 작업 순서

1. §11 순서 1부터 시작한다. 각 순서의 "완료 확인"을 실제로 실행해 통과한 뒤 다음으로 넘어간다. 통과하지 못하면 다음 순서 코드를 쓰지 않는다.
2. 순서마다 git commit 한다. 메시지: `step N: <내용>`.
3. 실험(E0~E7, E6b)은 실제로 실행하고 `reports/`에 §8 형식으로 기록한다. 실행하지 않은 실험을 기록하지 않는다.
4. README의 "결정 기록" 섹션에, 이 문서에 없어서 스스로 정한 값과 §7 "2단계 검증 항목"의 실측 결과를 남긴다.

### 범위 규칙

- §2 "제외하는 것"에 있는 것은 만들지 않는다. 필요해 보여도 README에 후속 항목으로만 적는다.
- §0 표의 확정값을 바꾸지 않는다. 실측 결과 바꿔야 하면 바꾼 이유와 관측을 결정 기록에 남긴다.
- 태스크는 `jobs.compute` 하나. 큐는 하나. 발행자는 하나. 워커는 concurrency=1 (E6b에서만 2, R13).
- Alembic, result backend, Celery beat, Celery retry, BackgroundTasks, 관리 API를 추가하지 않는다.
- 문서에 없는 결정이 필요하면 가장 단순한 선택을 하고 결정 기록에 남긴다. 사용자에게 질문하기 위해 멈추지 않는다.

### 코드 규칙

- Python 3.12. 타입 힌트 사용. 동기 SQLAlchemy 2.x 스타일 (`select()`, `update()`, `Session`).
- requirements.txt는 `패키지==버전`으로 핀. 생성 시점의 최신 안정판을 `pip index versions` 등으로 확인해 고정하고, 확인한 버전 목록을 README에 표로 남긴다. 추측한 버전 번호를 쓰지 않는다.
- 로그는 표준 `logging`, 한 줄에 `phase job_id event_id execution_id task_id attempt`를 키=값으로 포함한다.
- 비밀번호·AWS 키를 코드·compose·README·리포트에 쓰지 않는다. `.env`는 `.gitignore`에 넣는다.
- 장애 주입 플래그(§0 v2 #1, #2)와 E0 플래그(R12)는 환경변수로만 읽는다. HTTP로 켜고 끄는 경로를 만들지 않는다.
- `psycopg[binary]` 핀 (R3). 설정은 `os.environ` 직접 읽기 (R4).
- `.gitattributes`에 `* text=auto eol=lf`. compose `command:`는 exec 배열 형식. `PYTHONUNBUFFERED=1` (R7).

### 검증 규칙

- E2·E7의 "행 수 불변"은 `run.py count` 또는 psql로 실제 조회해 기록한다.
- E5·E6의 "채택 1회"는 워커 로그의 `adopted` / `already_done` 라인과 `result.execution_id` 비교로 기록한다. `rejected_already_done`은 E6b에서만 나오며, 그 라인이 없으면 조건부 UPDATE를 검증한 것이 아니다.
- SQS 항목은 실제 연결이 없으면 체크리스트에 "미실행"으로 남긴다. Redis 결과로 대신하지 않는다.
- 리포트마다 §8 "한계" 고정 항목을 빠뜨리지 않는다.

### 완료 보고

1~4단계 완료 시 README에 아래를 남긴다.

- 실행 방법 (compose 명령, run.py 사용법)
- 패키지·이미지 버전 표
- 결정 기록
- E0~E7(E6b 포함) 리포트 링크와 통과 여부
- 후속 항목 (§13)
- 면접 정리 문서와의 대응, Django 대응표 (§14)

---

## 13. 후속 논의 항목

1. 다중 발행자 — `FOR UPDATE SKIP LOCKED` vs 점유 만료. 전송 중 락 유지 여부.
2. ~~`task_acks_late=True` + `task_reject_on_worker_lost` + SQS visibility timeout의 관계. 워커 강제 종료 실험.~~ → **6단계 E8·E9로 승격 (R17)**
3. ~~`SENT + PENDING` 탐지 — 실행 시작 기록(점유 상태·시각·토큰)과 오래된 미완료 자동 재발행.~~ → **7단계로 승격 (R20~R22).** 자동 재발행이 아니라 수동 `reconcile`로 축소
4. 커밋 직후 즉시 발행 + 주기 복구 혼합, 두 경로의 경합.
5. DB 밖 부작용(파일 저장) 추가 시 멱등성 범위 — 실행별 경로 + DB 채택 포인터.
6. 빠른 작업·느린 작업의 큐·워커 분리 비교.
7. **E8** — 워커 강제 종료(`docker compose kill worker`) × `task_acks_late` × Redis `visibility_timeout`. False면 메시지 소실·`SENT + PENDING` 영구 잔류, True면 visibility_timeout 후 재전달. 면접 정리 Q7·7.5절의 실증. `CELERY_ACKS_LATE`·`REDIS_VISIBILITY_TIMEOUT`을 env로 빼면 같은 코드로 양쪽을 본다. 2번과 합쳐 진행 (U4로 이번 범위에서 제외). → **6단계로 승격 (R17). 단 Redis 대조는 제외 — SQS 전용**
8. `outbox_events.job_id UNIQUE` 해제 + `version` — 재발행 버전마다 새 이벤트 행. 이번 MVP에서 `republish`가 행을 만들지 않는 이유가 이 제약이다.

이번 학습 결과는 **결론 → 제약 → 대안 → 대조** 순서로 설명한다. 기존 보험 프로젝트의 실제 구현과 이번 실습을 구분한다.

---

## 14. 면접 정리 문서와의 대응

`insurance_message_queue_interview_notes_2026-09-16.md`가 남긴 공백·미확인 사항 중 이 MVP가 손으로 확인하는 것. 나머지는 §13 후속이다. 이 표의 "대응"은 학습 실습이지 과거 프로젝트의 구현이 아니다.

| 면접 정리의 공백·미확인 | 이 MVP의 대응 | 실험 |
|---|---|---|
| "큐에 전달되기 전에 누락된 작업은 PDF 오류 알림 자체가 없다" (8.7). 큐 등록 누락 자동 탐지 여부 미확인 (1절) | 업무와 발행 요청을 같은 트랜잭션에 → `outbox.PENDING` 행이 탐지 근거. E0이 "근거 없음"을, E3가 "근거 있음"을 보여준다 | E0, E2, E3, E4 |
| "전송 후 기록 전 중단 → 중복 전달 → 소비자 멱등성" (Q4) | `PUBLISHER_CRASH_AFTER_SEND` + 조건부 UPDATE 채택 | E5, E6, E6b |
| "DB 중복 INSERT 거절 ≠ API가 기존 성공 결과 반환. 후자의 실제 처리는 미확인" (8.8) | `request_key` IntegrityError → 200 동일 / 409 상이 | E7 |
| "실패 로그만 복구 근거로 쓰면 로그 전 종료를 놓친다 / 처리할 일을 먼저 기록하고 미완료를 찾는다" (3·5절) | 발행자는 로그가 아니라 PENDING 행을 조회한다 | E3, E4, E5 |
| "발행자가 죽으면? → 적체 감지 필요" (Q4 꼬리), "미전송 건수·최장 대기 시간 관측" (Q18) | `run.py backlog` | E0, E3, E5 |
| "사전 조회만으로 동시 실행을 막을 수 없다" (3절), 조건부 UPDATE rowcount 0/1 (Q6, 7.6) | 워커의 사전 조회는 최적화, 보장은 `UPDATE ... WHERE status='PENDING'` | E6b |
| "같은 S3 경로 덮어쓰기 ≠ 최신성" (8.8) → "실행별 경로 + DB 채택 포인터" (9.4~9.5) | `result.execution_id` + 조건부 UPDATE — S3 없이 같은 원리를 DB 결과로 | E5, E6b |
| Celery retry ≠ 브로커 재전달 ≠ 수동 재실행 (7.5) | retry 없음. `republish` = 수동 재실행. 브로커 재전달은 E8(후속) | E6 |
| SQS visibility timeout·ACK 설정 미확인 (Q4, Q7, 7.5) | 이번 범위 밖. `acks_late=False` 명시. E8 후속 | — |
| 점유 토큰·lease·문서 버전 (Q5, 9절) | 이번 범위 밖. `SENT + PENDING`을 구분 못 하는 한계로 §10에 기록 | — |

### Django 대응표

면접 정리는 Django 기준이고 MVP는 FastAPI + SQLAlchemy다. 개념은 같다. 스택을 FastAPI로 둔 이유는 세션 commit을 손으로 쓰는 쪽이 트랜잭션 경계 학습에 낫고, 과거 구성을 복원하지 않는다는 원칙 때문이다.

| 이 MVP | Django |
|---|---|
| `with session_scope() as s:` 안의 jobs·outbox INSERT → commit | `with transaction.atomic():` 안의 두 `objects.create()` (7.7절) |
| `app/publisher.py` 독립 루프 컨테이너 | `python manage.py run_outbox` 커스텀 관리 명령을 상시 프로세스로 (5절) |
| `s.execute(update(Job).where(...)).rowcount` | `Job.objects.filter(id=..., status="PENDING").update(...)`의 반환값 (9.2절) |
| E0의 "커밋 후 API가 직접 apply_async" | `transaction.on_commit(lambda: task.delay(...))` — 콜백은 영속 기록이 아니다 (Q4 꼬리) |
| `IntegrityError` → rollback → 재조회 | `IntegrityError` → `atomic()` 블록 밖에서 재조회 |
| `func.now()` | `django.db.models.functions.Now()` |

---

## 참고

- [AWS Transactional outbox pattern](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)
- [Celery SQS](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/sqs.html)
- [Celery Redis](https://docs.celeryq.dev/en/stable/getting-started/backends-and-brokers/redis.html)
- [Celery Configuration](https://docs.celeryq.dev/en/stable/userguide/configuration.html)
- [SQLAlchemy 트랜잭션](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
- [PostgreSQL Read Committed](https://www.postgresql.org/docs/current/transaction-iso.html#XACT-READ-COMMITTED)

이 문서는 구현 방향의 확정본이며 실험 성공 결과가 아니다. 패키지·브로커·DB 버전과 실측 설정은 README에 기록한다.