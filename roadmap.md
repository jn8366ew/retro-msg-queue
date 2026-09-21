# 로드맵 — 메시지큐 학습 MVP

- 기준 문서: `dev-plan.md` v3 (2026-09-20). 상세는 §11, 실험 절차는 §8, 결정 번호(U·R)는 §0.
- 규칙: 각 항목은 실제로 실행·통과했을 때만 체크한다. 단계 끝마다 `step N:` 커밋, 사용자 확인 후 다음 단계로 (U7).
- 이 파일은 진행 상태만 기록한다. 설계·결정의 변경은 `dev-plan.md`에 쓴다.

## 0. 사전 준비

- [x] Docker Desktop 기동 (`docker info` 성공)
- [x] `git init` → 첫 커밋: `dev-plan.md`, `roadmap.md`, `insurance_message_queue_interview_notes_2026-09-16.md` (R8)
- [x] 패키지 최신 안정판 재조회 → `requirements.txt` `==` 핀, README 버전 표 (§12)

## 1단계 — 접수·조회·롤백 (워커·발행자 없음)

- [x] `.gitignore`, `.gitattributes`(R7), `.env.example` → `.env`, `pyproject.toml`
- [x] `Dockerfile`: python:3.12-slim, pycurl 빌드 의존성(R15), `COPY app/ experiments/ tests/`, `PYTHONUNBUFFERED=1`
- [x] `compose.yaml`: postgres(healthcheck R2, `pgdata`, initdb로 `app_test` R5) + api(`restart: "no"`, exec 형식 command)
- [x] `app/config.py`(os.environ, R4), `app/db.py`(advisory lock `init_db`, R1), `app/models.py`(§4)
- [x] `app/main.py`: `POST /jobs`, `GET /jobs/{id}`, `API_CRASH_BEFORE_OUTBOX` (§5)
- [x] `experiments/run.py`: `create`, `get`, `count`
- [x] `tests/test_api.py`: 202 / 200 동일 / 409 / 500 롤백 / 404 통과
- [x] **E7** 실행 → `reports/E7-<날짜>-1.md`
- [x] **E2** 실행 → `reports/E2-<날짜>-1.md` (브로커 항목은 구조적 보장으로 명시)
- [x] README 초안: 실행 방법, 버전 표, 결정 기록 R1~R8
- [x] `git commit -m "step 1: ..."`

## 2단계 — 브로커·워커·발행자 (정상 흐름)

- [x] `requirements.txt`에 `celery[sqs]`, `redis` 추가
- [x] `app/celery_app.py`: §7 설정, `BROKER_KIND` 분기
- [x] `app/tasks.py`: `compute`(`bind=True`, `func.now()`, R11), `send_compute`
- [x] `app/publisher.py`: §6 루프, SIGTERM/SIGINT 핸들러, `kombu.exceptions.OperationalError` 처리
- [x] compose: redis(profile `redis`), publisher, worker — `depends_on: postgres`만 (R10)
- [x] `run.py wait`
- [x] `tests/test_adopt.py`: 조건부 UPDATE 채택 1 / 거절 0
- [x] **§7 2단계 검증 실측**: redis 중지 상태에서 `send_compute` 소요 초·예외 클래스·메시지 기록 (예상 ≈6초 `OperationalError`)
- [x] `send_compute` 전용 연결에 `max_retries: 0` 적용 → 재실측(<100ms) → 결정 기록 (R9)
- [x] **E1** 실행 → 리포트
- [x] `git commit -m "step 2: ..."`

## 3단계 — 장애 주입·대조군·관측

- [x] `PUBLISHER_CRASH_AFTER_SEND` (§6)
- [x] E0 플래그 `LEGACY_INLINE_PUBLISH`, `API_CRASH_AFTER_COMMIT` (R12), GET `outbox: null`
- [x] `run.py republish`, `run.py backlog` (U3)
- [x] **E0** 실행 → 리포트 (jobs.PENDING만, `outbox: null`, backlog 0, 브로커 0, 워커 로그 없음)
- [x] **E3** 실행 → 리포트 (PENDING/PENDING → backlog 1 → 재시작 후 SENT·DONE)
- [x] **E4** 실행 → 리포트 (attempts≥2, last_error, 복구 후 전달)
- [x] `git commit -m "step 3: ..."`

## 4단계 — 중단·중복

- [x] **E5** (사전 backlog 0 확인) 실행 → 리포트 (DONE-before-SENT, 같은 event_id 재발행, `adopted` 1 + `already_done` 1)
- [x] **E6** 실행 → 리포트 (`already_done` 2건, execution_id 불변)
- [x] **E6b** concurrency=2, `TASK_DELAY_SEC=3` (R13) 실행 → 리포트 (`adopted` 1 + `rejected_already_done` 1)
- [x] 리포트 9개 모두 "한계" 고정 항목 포함
- [x] README 완성: 결정 기록 R1~R16 + 실측값, 실험 표·링크, §13 후속, §14 대응표
- [x] `dev-plan.md` §8 완료 체크리스트 전부 체크
- [x] `git commit -m "step 4: ..."` → **로컬 아웃박스 MVP 완료**

## 5단계 — SQS (선택, 사용자 준비 후)

- [x] 사용자 준비: Standard 큐(이름 = `CELERY_QUEUE`), 리전, 큐 URL, IAM 권한 (§9)
- [x] `BROKER_KIND=sqs` 시작 검증: 빈 값·큐 URL 불일치 시 기동 실패 (R14)
- [x] E1·E3·E5를 SQS로 재실행 → 리포트
- [x] README SQS 절: 권한, 연결 오류 확인법, visibility timeout·ACK 기록
- [x] `git commit -m "step 5: ..."` → **SQS 전환 완료 (E1·E3·E5)**

## 6단계 — 브로커 재전달·DLQ (SQS 전용, R17~R19)

완료 (2026-09-21). [E8](reports/E8-20260921-1.md) · [E9](reports/E9-20260921-1.md).

- [x] `TASK_ACKS_LATE`·`TASK_REJECT_ON_WORKER_LOST`·`TASK_CRASH_BEFORE_ADOPT` env 분기 (기본 0, pytest 18건 통과)
- [x] **E8** 실행 → 리포트 — 대조군(acks_late=0) 재전달 0건·SENT+PENDING 영구 잔류 / 본실험(=1) **30.003초 후 같은 task_id로 재전달**, DONE
- [x] 사용자 준비: `jobs-dlq` Standard 큐, `jobs`에 redrive(maxReceiveCount=3), IAM Resource에 DLQ ARN 추가
- [x] **E9** 실행 → 리포트 — 수신 3회 후 DLQ 이동, jobs.PENDING 잔류. 재전달 간격 **10.07·10.12초 = `wait_time_seconds`** (큐 VT 30초 아님)
- [x] api·publisher 이미지 재빌드 (`--build` — worker만 새 코드로 빌드된 상태)
- [x] README: 실험 표·결정 기록 반영 + 첫 화면 재배치(A안 — 한 문장·실험 표·실측 하이라이트·비보장 요약을 상단으로)
- [x] `git commit -m "step 6: ..."` → **브로커 재전달·DLQ 완료 (E8·E9)**

## 후속 (이번 범위 밖, §13)

- [x] ~~E8 — 워커 kill × `acks_late` × `visibility_timeout`~~ → 6단계에서 완료 (SQS 전용, E9 DLQ 포함)
- [ ] `outbox.job_id UNIQUE` 해제 + `version`
- [ ] 다중 발행자 SKIP LOCKED / 즉시 발행 + 주기 복구 / `SENT + PENDING` 탐지
