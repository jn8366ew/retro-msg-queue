"""POST /jobs, GET /jobs/{id} 계약 (dev-plan §5). E2·E7의 단위 버전."""

from app.config import settings


def test_create_returns_202_and_writes_both_rows(client, counts):
    r = client.post("/jobs", json={"request_key": "t-create", "value": 7})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "PENDING"
    assert counts() == (1, 1)

    g = client.get(f"/jobs/{body['job_id']}")
    assert g.status_code == 200
    d = g.json()
    assert d["input"] == {"value": 7}
    assert d["result"] is None
    assert d["completed_at"] is None
    assert d["outbox"]["status"] == "PENDING"
    assert d["outbox"]["attempts"] == 0
    assert d["outbox"]["published_at"] is None


def test_duplicate_same_input_returns_200_same_job(client, counts):
    first = client.post("/jobs", json={"request_key": "t-dup", "value": 3})
    assert first.status_code == 202
    second = client.post("/jobs", json={"request_key": "t-dup", "value": 3})
    assert second.status_code == 200
    d = second.json()
    assert d["job_id"] == first.json()["job_id"]
    assert d["outbox"] is not None  # 200 본문은 GET과 같은 형태
    assert counts() == (1, 1)


def test_duplicate_different_input_returns_409(client, counts):
    assert client.post("/jobs", json={"request_key": "t-conf", "value": 3}).status_code == 202
    r = client.post("/jobs", json={"request_key": "t-conf", "value": 4})
    assert r.status_code == 409
    assert r.json() == {"detail": "request_key already used with different input"}
    assert counts() == (1, 1)


def test_crash_before_outbox_rolls_back_everything(client, counts, monkeypatch):
    monkeypatch.setattr(settings, "api_crash_before_outbox", True)
    r = client.post("/jobs", json={"request_key": "t-crash", "value": 5})
    assert r.status_code == 500
    assert counts() == (0, 0)

    # 플래그를 끄면 같은 request_key로 정상 접수된다 — jobs 행이 남아 있지 않았다는 증거
    monkeypatch.setattr(settings, "api_crash_before_outbox", False)
    r2 = client.post("/jobs", json={"request_key": "t-crash", "value": 5})
    assert r2.status_code == 202
    assert counts() == (1, 1)


def test_get_unknown_job_returns_404(client):
    r = client.get("/jobs/999999")
    assert r.status_code == 404


def test_validation_422(client, counts):
    assert client.post("/jobs", json={"request_key": "", "value": 1}).status_code == 422
    assert client.post("/jobs", json={"request_key": "x" * 101, "value": 1}).status_code == 422
    assert client.post("/jobs", json={"request_key": "ok", "value": "seven"}).status_code == 422
    assert client.post("/jobs", json={"request_key": "ok"}).status_code == 422
    assert counts() == (0, 0)
