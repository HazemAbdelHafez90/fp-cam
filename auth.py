"""Basic shared-password authentication helpers for CAM."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


AUTH_COOKIE_NAME = "cam_session"
AUTH_COOKIE_VALUE = "authenticated"
DEFAULT_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


@dataclass(frozen=True)
class AuthConfig:
    password: str
    session_secret: str
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS
    cookie_secure: bool = False


def load_auth_config() -> AuthConfig:
    cookie_secure = (
        os.getenv("VERCEL") == "1"
        or os.getenv("CAM_AUTH_COOKIE_SECURE", "").lower() == "true"
    )
    return AuthConfig(
        password=os.getenv("CAM_AUTH_PASSWORD", ""),
        session_secret=os.getenv("CAM_SESSION_SECRET", ""),
        cookie_secure=cookie_secure,
    )


def is_configured(config: AuthConfig) -> bool:
    return bool(config.password and config.session_secret)


def is_password_valid(candidate: str, config: AuthConfig) -> bool:
    if not is_configured(config):
        return False
    return hmac.compare_digest(candidate, config.password)


def _serializer(config: AuthConfig) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(config.session_secret, salt="cam-auth-session")


def create_session_token(config: AuthConfig) -> str:
    if not config.session_secret:
        raise RuntimeError("CAM_SESSION_SECRET must be configured")
    return _serializer(config).dumps(AUTH_COOKIE_VALUE)


def verify_session_token(token: str | None, config: AuthConfig) -> bool:
    if not token or not is_configured(config) or config.max_age_seconds < 0:
        return False
    try:
        value = _serializer(config).loads(token, max_age=config.max_age_seconds)
    except (BadSignature, SignatureExpired):
        return False
    return value == AUTH_COOKIE_VALUE
