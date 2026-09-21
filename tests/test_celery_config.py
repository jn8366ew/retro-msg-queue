"""BROKER_KIND=sqs 시작 검증 (dev-plan §9, R14).

실제 AWS 연결 없이 설정 분기만 확인한다. 큐 이름과 predefined_queues 키가 어긋나면
kombu는 발행 시점에야 UndefinedQueueException을 던진다 — 그 전에 기동을 막는 것이 R14다.
"""

import pytest

from app.celery_app import _broker_url_and_options, settings

_URL = "https://sqs.ap-northeast-2.amazonaws.com/123456789012/jobs"


@pytest.fixture
def sqs(monkeypatch):
    monkeypatch.setattr(settings, "broker_kind", "sqs")
    monkeypatch.setattr(settings, "celery_queue", "jobs")
    monkeypatch.setattr(settings, "sqs_region", "ap-northeast-2")
    monkeypatch.setattr(settings, "sqs_queue_url", _URL)


def test_sqs_options(sqs):
    url, options = _broker_url_and_options()
    assert url == "sqs://"  # 자격 증명은 URL이 아니라 AWS 표준 체인에서 온다
    assert options["region"] == "ap-northeast-2"
    assert options["predefined_queues"] == {"jobs": {"url": _URL}}


@pytest.mark.parametrize("field", ["sqs_region", "sqs_queue_url"])
def test_missing_field_fails(sqs, monkeypatch, field):
    monkeypatch.setattr(settings, field, "")
    with pytest.raises(RuntimeError, match="requires SQS_REGION and SQS_QUEUE_URL"):
        _broker_url_and_options()


def test_queue_name_mismatch_fails(sqs, monkeypatch):
    """큐 URL의 이름과 CELERY_QUEUE가 다르면 기동하지 않는다."""
    monkeypatch.setattr(settings, "celery_queue", "orders")
    with pytest.raises(RuntimeError, match="must end with /orders"):
        _broker_url_and_options()


def test_trailing_slash_accepted(sqs, monkeypatch):
    monkeypatch.setattr(settings, "sqs_queue_url", _URL + "/")
    _, options = _broker_url_and_options()
    assert options["predefined_queues"]["jobs"]["url"] == _URL + "/"


def test_unknown_broker_kind_fails(monkeypatch):
    monkeypatch.setattr(settings, "broker_kind", "kafka")
    with pytest.raises(RuntimeError, match="must be 'redis' or 'sqs'"):
        _broker_url_and_options()


def test_redis_branch_unchanged(monkeypatch):
    monkeypatch.setattr(settings, "broker_kind", "redis")
    monkeypatch.setattr(settings, "redis_url", "redis://redis:6379/0")
    url, options = _broker_url_and_options()
    assert url == "redis://redis:6379/0"
    assert options == {"visibility_timeout": 3600}
