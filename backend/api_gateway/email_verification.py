import hashlib
import hmac
import os
import secrets
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

import redis


class EmailVerificationError(RuntimeError):
    pass


class EmailRateLimited(EmailVerificationError):
    pass


class EmailDeliveryError(EmailVerificationError):
    pass


class InvalidVerificationCode(EmailVerificationError):
    pass


class VerificationAttemptsExceeded(EmailVerificationError):
    pass


class EmailVerificationService:
    def __init__(
        self,
        redis_client,
        *,
        secret: str,
        smtp_host: str,
        smtp_port: int,
        smtp_username: str,
        smtp_password: str,
        smtp_from: str,
        smtp_from_name: str,
        smtp_use_ssl: bool,
        ttl_seconds: int = 600,
        cooldown_seconds: int = 60,
        max_attempts: int = 5,
        smtp_timeout_seconds: int = 10,
    ):
        self.redis = redis_client
        self.secret = secret
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_from = smtp_from or smtp_username
        self.smtp_from_name = smtp_from_name
        self.smtp_use_ssl = smtp_use_ssl
        self.ttl_seconds = ttl_seconds
        self.cooldown_seconds = cooldown_seconds
        self.max_attempts = max_attempts
        self.smtp_timeout_seconds = smtp_timeout_seconds

    @classmethod
    def from_env(cls) -> "EmailVerificationService":
        redis_client = redis.from_url(
            os.getenv("REDIS_URL", "redis://redis:6379/2"),
            decode_responses=True,
        )
        return cls(
            redis_client,
            secret=os.getenv("JWT_SECRET", "change-me-in-production"),
            smtp_host=os.getenv("SMTP_HOST", "smtp.qq.com"),
            smtp_port=int(os.getenv("SMTP_PORT", "465")),
            smtp_username=os.getenv("SMTP_USERNAME", ""),
            smtp_password=os.getenv("SMTP_PASSWORD", ""),
            smtp_from=os.getenv("SMTP_FROM", ""),
            smtp_from_name=os.getenv("SMTP_FROM_NAME", "AI Writing Platform"),
            smtp_use_ssl=os.getenv("SMTP_USE_SSL", "true").lower() in {"1", "true", "yes"},
            ttl_seconds=int(os.getenv("EMAIL_CODE_TTL_SECONDS", "600")),
            cooldown_seconds=int(os.getenv("EMAIL_CODE_COOLDOWN_SECONDS", "60")),
            max_attempts=int(os.getenv("EMAIL_CODE_MAX_ATTEMPTS", "5")),
            smtp_timeout_seconds=int(os.getenv("SMTP_TIMEOUT_SECONDS", "10")),
        )

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    def _email_id(self, email: str) -> str:
        return hashlib.sha256(self.normalize_email(email).encode()).hexdigest()

    def _code_key(self, email: str) -> str:
        return f"email-verification:code:{self._email_id(email)}"

    def _cooldown_key(self, email: str) -> str:
        return f"email-verification:cooldown:{self._email_id(email)}"

    def _attempts_key(self, email: str) -> str:
        return f"email-verification:attempts:{self._email_id(email)}"

    def _digest(self, email: str, code: str) -> str:
        value = f"{self.normalize_email(email)}:{code}:{self.secret}"
        return hashlib.sha256(value.encode()).hexdigest()

    def send_code(self, email: str) -> int:
        email = self.normalize_email(email)
        cooldown_key = self._cooldown_key(email)
        if not self.redis.set(cooldown_key, "1", ex=self.cooldown_seconds, nx=True):
            raise EmailRateLimited("Please wait before requesting another code.")

        code = f"{secrets.randbelow(1_000_000):06d}"
        code_key = self._code_key(email)
        attempts_key = self._attempts_key(email)
        self.redis.set(code_key, self._digest(email, code), ex=self.ttl_seconds)
        self.redis.delete(attempts_key)

        try:
            self._deliver(email, code)
        except Exception as exc:
            self.redis.delete(code_key, attempts_key, cooldown_key)
            raise EmailDeliveryError("Verification email could not be sent.") from exc
        return self.ttl_seconds

    def verify_code(self, email: str, code: str) -> None:
        email = self.normalize_email(email)
        code_key = self._code_key(email)
        attempts_key = self._attempts_key(email)
        expected = self.redis.get(code_key)
        if not expected:
            raise InvalidVerificationCode("Verification code is invalid or expired.")

        if hmac.compare_digest(expected, self._digest(email, code)):
            return

        attempts = self.redis.incr(attempts_key)
        if attempts == 1:
            self.redis.expire(attempts_key, self.ttl_seconds)
        if attempts >= self.max_attempts:
            self.redis.delete(code_key, attempts_key)
            raise VerificationAttemptsExceeded("Too many incorrect attempts. Request a new code.")
        raise InvalidVerificationCode("Verification code is invalid or expired.")

    def consume_code(self, email: str) -> None:
        self.redis.delete(
            self._code_key(email),
            self._attempts_key(email),
            self._cooldown_key(email),
        )

    def _deliver(self, recipient: str, code: str) -> None:
        if not all((self.smtp_host, self.smtp_username, self.smtp_password, self.smtp_from)):
            raise EmailDeliveryError("SMTP is not configured.")

        message = EmailMessage()
        message["Subject"] = "AI Writing Platform verification code"
        message["From"] = formataddr((self.smtp_from_name, self.smtp_from))
        message["To"] = recipient
        message.set_content(
            "Your verification code is:\n\n"
            f"{code}\n\n"
            f"This code expires in {self.ttl_seconds // 60} minutes. "
            "If you did not request it, you can ignore this email."
        )

        if self.smtp_use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                self.smtp_host,
                self.smtp_port,
                timeout=self.smtp_timeout_seconds,
                context=context,
            ) as smtp:
                smtp.login(self.smtp_username, self.smtp_password)
                smtp.send_message(message)
            return

        with smtplib.SMTP(
            self.smtp_host,
            self.smtp_port,
            timeout=self.smtp_timeout_seconds,
        ) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(self.smtp_username, self.smtp_password)
            smtp.send_message(message)
