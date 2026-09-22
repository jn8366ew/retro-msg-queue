# 7단계 실습 — `SENT + PENDING` 탐지와 복구 (E10 · E11)

직접 돌려 보는 실습서다. 구현과 단위 테스트(25건)는 끝나 있고, 실험 본편은 아직 아무도 돌리지 않았다.
설계 근거는 [dev-plan.md](../dev-plan.md) R20~R22, 이전 실험은 [E8](../reports/E8-20260921-1.md)·[E9](../reports/E9-20260921-1.md).

## 이 실습으로 확인하는 것

1~6단계의 모든 리포트는 같은 한계로 끝났다: DB에서 `outbox.SENT + jobs.PENDING`은 한 가지 모양인데, 실제로는 서로 다른 상황이 섞여 있다. 7단계는 워커가 실행을 **시작할 때** `job_executions`에 한 줄을 먼저 커밋하고, 끝날 때 결과를 채운다. 그 기록으로 네 갈래를 나눈다.

| 분류 | 기준 | 뜻 |
|---|---|---|
| `not_started` | 실행 기록 없음 | 워커가 아직 안 받았다 |
| `running` | 마지막 시작이 60초 미만 전 | 기다린다 |
| `stalled` | 마지막 시작이 60초 이상 전 | `reconcile` 대상 |
| `gave_up` | 끝나지 않은 실행 3건 이상 | 다시 보내지 않는다 |

## 순서와 소요 시간

E10 ① → ② → ③ → **E11ⓐ**(③의 상태를 이어 씀) → E10 ④ → **E11ⓒ**(④를 이어 씀) → **E11ⓑ** → 원복. 전체 약 15분. E11ⓑ만 4분 남짓 걸린다.

각 단계마다 **출력을 복사해 두었다가** 끝나고 넘겨주면 리포트를 쓴다.

---

## 0. 준비

포트 8000을 다른 프로젝트가 쓰고 있으면 옮긴다.

```powershell
$env:API_PORT="8001"
```

```powershell
docker compose up -d --build api publisher worker
```

`.env`가 `BROKER_KIND=sqs`인지, 워커가 기본 설정인지 확인한다.

```powershell
docker compose exec worker python -c `
  "from app.celery_app import app; from app.config import settings as s; print(app.conf.broker_url, app.conf.task_acks_late, s.task_crash_before_adopt, s.task_delay_sec, s.stale_after_sec)"
```

> 보여야 할 것: `sqs:// False False 1.0 60.0`

### 자주 쓰는 관측 명령

**분류 보기** — 이 실습의 주인공이다. `--detail`은 업무별 근거를 붙인다.

```powershell
docker compose exec api python experiments/run.py backlog --detail
```

**실행 기록 보기**

```powershell
docker compose exec postgres psql -U app -d app `
  -c "SELECT job_id, left(execution_id::text,8) AS exec, left(task_id,8) AS task, outcome, started_at, finished_at FROM job_executions ORDER BY started_at;"
```

**워커 로그**

```powershell
docker compose logs worker | Select-String "phase="
```

**초기화** — 각 실험 앞에서 쓴다. `job_executions`도 함께 비워진다.

```powershell
docker compose exec postgres psql -U app -d app `
  -c "TRUNCATE outbox_events, jobs RESTART IDENTITY CASCADE;"
```

---

## E10 — 같은 `SENT + PENDING`을 네 갈래로 나눠 보인다

### ① `not_started` — 워커가 없다

초기화한 뒤 워커를 내린다.

```powershell
docker compose stop worker
```

```powershell
docker compose exec api python experiments/run.py create `
  --request-key E10-1 --value 7
```

발행자가 보낼 시간(1~2초)을 준 뒤:

```powershell
docker compose exec api python experiments/run.py backlog --detail
```

> 보여야 할 것: `sent_but_job_pending: 1`, `by_state.not_started: 1`, `executions=0 last_start_age=-`

워커를 다시 올리면 끝나야 한다.

```powershell
docker compose start worker
```

```powershell
docker compose exec api python experiments/run.py wait --job-id 1 --timeout 30
```

**생각해 볼 것**

- 워커가 없는 동안 메시지는 어디에 있었나? (`aws sqs get-queue-attributes ... ApproximateNumberOfMessages`로 확인해 볼 수 있다. 근사값이다)
- `not_started`에는 60초 기준이 적용되지 않는다. **메시지가 워커에게 닿기 전에 사라지면** 이 업무는 어떻게 되나? → 아래 ①-심화

#### ①-심화 (선택) — 받기 전에 사라진 메시지

워커를 내린 채로 접수한 뒤, 큐를 비운다. **퍼지는 되돌릴 수 없고 60초에 한 번만 된다.** 다른 메시지가 없을 때만 한다.

```powershell
docker compose stop worker
```

```powershell
docker compose exec api python experiments/run.py create `
  --request-key E10-1b --value 7
```

```powershell
aws sqs purge-queue --profile retro-msg-queue --region ap-northeast-2 `
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/<계정ID>/jobs
```

```powershell
docker compose start worker
```

1분 넘게 기다린 뒤 `backlog --detail`과 `reconcile`을 차례로 본다.

```powershell
docker compose exec api python experiments/run.py reconcile
```

**생각해 볼 것** — 이 업무는 끝나는가? `reconcile`은 이것을 구하는가? 이 설계에서 빠진 칸은 무엇이고, 채우려면 어떤 기준(무엇의 나이)이 필요한가?

### ② `running` — 정상 실행 중

초기화한 뒤, 계산을 20초로 늘린다.

```powershell
$env:TASK_DELAY_SEC="20"
docker compose up -d --force-recreate worker
```

```powershell
docker compose exec api python experiments/run.py create `
  --request-key E10-2 --value 7
```

20초 안에:

```powershell
docker compose exec api python experiments/run.py backlog --detail
```

> 보여야 할 것: `by_state.running: 1`, `unfinished=1`, `last_start_age`가 20초 미만

20초가 지나면 `by_state`가 전부 0이 돼야 한다. 실행 기록도 한 번 본다 — `outcome=adopted`, `finished_at`이 채워져 있어야 한다.

**생각해 볼 것** — 기준이 60초인데 정상 작업이 90초 걸리면 어떻게 분류되나? 그 상태에서 `reconcile`을 돌리면 무슨 일이 생기고, 그게 왜 위험하지 **않은가**(또는 무엇이 낭비인가)?

### ③ `stalled` — 실행 중에 워커가 죽었다 (E8 대조군 재현)

②의 업무가 끝난 뒤(20초) 초기화한다. 건너뛰면 ②의 업무가 ③의 kill에 함께 끊긴다.

```powershell
docker compose exec postgres psql -U app -d app `
  -c "TRUNCATE outbox_events, jobs RESTART IDENTITY CASCADE;"
```

`TASK_DELAY_SEC=20`은 그대로 두고, `acks_late`는 기본값(False)이다.

```powershell
docker compose exec api python experiments/run.py create `
  --request-key E10-3 --value 7
```

실행이 시작되면(2~3초) 컨테이너째 죽인다.

```powershell
Start-Sleep -Seconds 3; docker compose kill worker
```

```powershell
docker compose up -d worker
```

죽인 직후와 **70초 뒤** 두 번 본다.

```powershell
docker compose exec api python experiments/run.py backlog --detail
```

```powershell
Start-Sleep -Seconds 70
```

```powershell
docker compose exec api python experiments/run.py backlog --detail
```

> 보여야 할 것: 처음엔 `running`, 70초 뒤엔 `stalled`. 두 번 모두 `executions=1 unfinished=1`. 그 사이 워커 로그에 새 `phase=start`는 없다

**생각해 볼 것** — DB만 보고 이 메시지가 **사라졌는지**, 브로커가 **다시 보낼 예정인지** 알 수 있나? E8 본실험(`acks_late=True`)이었다면 70초 뒤 분류는 무엇이었을까?

**초기화하지 말고** 바로 E11ⓐ로 간다.

---

## E11ⓐ — `stalled`를 복구한다

워커를 기본 설정으로 되돌린다.

```powershell
Remove-Item Env:\TASK_DELAY_SEC
docker compose up -d --force-recreate worker
```

```powershell
docker compose exec api python experiments/run.py reconcile
```

> 보여야 할 것: `"reconciled": [1]`

발행자가 다음 주기(1초)에 다시 보낸다.

```powershell
docker compose exec api python experiments/run.py wait --job-id 1 --timeout 30
```

그리고 실행 기록을 본다.

> 보여야 할 것: jobs `DONE`, outbox `attempts=2`, 실행 기록 2행 — 첫 행은 끝나지 않음(죽은 실행), 둘째 행 `adopted`. **`task_id`가 두 행에서 다르다**

**생각해 볼 것**

- `task_id`가 달라진 이유는? E8 본실험(브로커 재전달)과 무엇이 다른가?
- 성공했는데 outbox `last_error`가 `reconciled: stalled`로 남아 있다. 버그인가, 기록인가?
- `reconcile`을 한 번 더 돌리면? (`"reconciled": []`여야 한다)

---

## E10 ④ — `gave_up` (E9 재현)

DLQ 설정(`jobs` → `jobs-dlq`, 최대 수신 수 3)이 살아 있어야 한다. 초기화한 뒤:

```powershell
$env:TASK_ACKS_LATE="1"; $env:TASK_REJECT_ON_WORKER_LOST="1"
$env:TASK_CRASH_BEFORE_ADOPT="1"; $env:TASK_DELAY_SEC="1"
docker compose up -d --force-recreate worker
```

```powershell
docker compose exec api python experiments/run.py create `
  --request-key E10-4 --value 7
```

약 40초 기다린다(재전달 간격 10초 × 2 + 여유).

```powershell
Start-Sleep -Seconds 40
```

```powershell
docker compose exec api python experiments/run.py backlog --detail
```

> 보여야 할 것: `by_state.gave_up: 1`, `executions=3 unfinished=3`. 실행 기록 3행의 **`task_id`가 모두 같다**

DLQ도 본다(근사값이라 흔들릴 수 있다).

```powershell
aws sqs get-queue-attributes --profile retro-msg-queue --region ap-northeast-2 `
  --queue-url https://sqs.ap-northeast-2.amazonaws.com/<계정ID>/jobs-dlq `
  --attribute-names ApproximateNumberOfMessages
```

**생각해 볼 것** — ③과 ④를 DB에서 가른 근거는 무엇 하나뿐인가? 그 근거는 확실한가?

**초기화하지 말고** 바로 E11ⓒ로 간다.

## E11ⓒ — DLQ에 격리된 업무는 되살리지 않는다

```powershell
docker compose exec api python experiments/run.py reconcile
```

> 보여야 할 것: `"reconciled": []`, `"gave_up_skipped": [1]`. outbox는 `SENT` 그대로

**생각해 볼 것** — 상한이 없었다면 이 `reconcile`은 무엇을 했을까? DLQ가 끊은 반복과 무슨 관계인가?

---

## E11ⓑ — DLQ가 없는 독약에서 상한이 멈추는가

`acks_late=False`면 워커는 받자마자 ACK하므로, 자식이 죽어도 브로커는 다시 보내지 않고 DLQ도 개입하지 않는다. 반복을 만드는 것은 **`reconcile`뿐**이다. 초기화한 뒤:

```powershell
Remove-Item Env:\TASK_ACKS_LATE; Remove-Item Env:\TASK_REJECT_ON_WORKER_LOST
docker compose up -d --force-recreate worker
```

(`TASK_CRASH_BEFORE_ADOPT=1`, `TASK_DELAY_SEC=1`은 남아 있다.)

```powershell
docker compose exec api python experiments/run.py create `
  --request-key E11-b --value 7
```

아래 두 명령을 **세 번** 반복한다. 매번 `backlog --detail`의 `unfinished`와 `reconcile` 출력을 적어 둔다.

```powershell
Start-Sleep -Seconds 65
```

```powershell
docker compose exec api python experiments/run.py backlog --detail
```

```powershell
docker compose exec api python experiments/run.py reconcile
```

> 보여야 할 것: 1회차 `unfinished=1` → `reconciled: [1]`, 2회차 `unfinished=2` → `reconciled: [1]`, 3회차 `unfinished=3` → `reconciled: []`, `gave_up_skipped: [1]`. jobs는 끝까지 `PENDING`

**생각해 볼 것** — 이 업무를 끝내려면 사람이 무엇을 해야 하나? 3이라는 숫자는 E9의 무엇과 짝을 이루나?

---

## 원복

```powershell
Remove-Item Env:\TASK_CRASH_BEFORE_ADOPT; Remove-Item Env:\TASK_DELAY_SEC
docker compose up -d --force-recreate worker
```

`Remove-Item`이 "찾을 수 없다"고 하면 이미 지워진 것이다. 실습이 끝났으면 폴링 요청을 멈추기 위해 내린다.

```powershell
docker compose down
```

## 넘겨줄 것

단계마다 복사해 둔 `backlog --detail`과 `reconcile` 출력, E11ⓐ·E10④의 실행 기록 조회 결과, 그리고 "생각해 볼 것"에 대한 답(한두 줄이면 된다). 예상과 다르게 나온 곳이 있으면 그게 가장 중요하다.
