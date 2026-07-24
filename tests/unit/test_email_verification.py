from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "backend" / "api_gateway"))

from email_verification import (  # noqa: E402
    EmailDeliveryError,
    EmailRateLimited,
    EmailVerificationService,
    InvalidVerificationCode,
    VerificationAttemptsExceeded,
)


class FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    def expire(self, key, seconds):
        return key in self.values


@pytest.fixture
def service(monkeypatch):
    instance = EmailVerificationService(
        FakeRedis(),
        secret="test-secret",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="sender@example.com",
        smtp_password="password",
        smtp_from="sender@example.com",
        smtp_from_name="Test Platform",
        smtp_use_ssl=True,
        ttl_seconds=600,
        cooldown_seconds=60,
        max_attempts=3,
    )
    monkeypatch.setattr(instance, "_deliver", lambda recipient, code: None)
    return instance


def test_send_and_verify_code_stores_only_digest(service, monkeypatch):
    monkeypatch.setattr("email_verification.secrets.randbelow", lambda limit: 123456)

    assert service.send_code(" User@Example.com ") == 600
    stored = service.redis.get(service._code_key("user@example.com"))

    assert stored != "123456"
    service.verify_code("user@example.com", "123456")


def test_send_code_enforces_cooldown(service):
    service.send_code("user@example.com")

    with pytest.raises(EmailRateLimited):
        service.send_code("user@example.com")


def test_wrong_codes_are_limited(service, monkeypatch):
    monkeypatch.setattr("email_verification.secrets.randbelow", lambda limit: 123456)
    service.send_code("user@example.com")

    with pytest.raises(InvalidVerificationCode):
        service.verify_code("user@example.com", "000000")
    with pytest.raises(InvalidVerificationCode):
        service.verify_code("user@example.com", "000001")
    with pytest.raises(VerificationAttemptsExceeded):
        service.verify_code("user@example.com", "000002")

    with pytest.raises(InvalidVerificationCode):
        service.verify_code("user@example.com", "123456")


def test_delivery_failure_clears_code_and_cooldown(service, monkeypatch):
    def fail_delivery(recipient, code):
        raise OSError("SMTP unavailable")

    monkeypatch.setattr(service, "_deliver", fail_delivery)

    with pytest.raises(EmailDeliveryError):
        service.send_code("user@example.com")

    assert service.redis.get(service._code_key("user@example.com")) is None
    assert service.redis.get(service._cooldown_key("user@example.com")) is None

    monkeypatch.setattr(service, "_deliver", lambda recipient, code: None)
    assert service.send_code("user@example.com") == 600


def test_consume_code_removes_verification_state(service, monkeypatch):
    monkeypatch.setattr("email_verification.secrets.randbelow", lambda limit: 123456)
    service.send_code("user@example.com")

    service.consume_code("user@example.com")

    assert service.redis.get(service._code_key("user@example.com")) is None
    assert service.redis.get(service._cooldown_key("user@example.com")) is None
