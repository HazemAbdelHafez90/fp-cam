import pytest

from auth import (
    AUTH_COOKIE_NAME,
    AuthConfig,
    create_session_token,
    is_password_valid,
    verify_session_token,
)


def test_password_validation_accepts_configured_password():
    config = AuthConfig(password="correct-password", session_secret="secret")

    assert is_password_valid("correct-password", config) is True


def test_password_validation_rejects_wrong_password():
    config = AuthConfig(password="correct-password", session_secret="secret")

    assert is_password_valid("wrong-password", config) is False


def test_password_validation_rejects_missing_config():
    config = AuthConfig(password="", session_secret="secret")

    assert is_password_valid("anything", config) is False


def test_password_validation_rejects_missing_session_secret():
    config = AuthConfig(password="correct-password", session_secret="")

    assert is_password_valid("correct-password", config) is False


def test_session_token_round_trip():
    config = AuthConfig(password="correct-password", session_secret="secret")

    token = create_session_token(config)

    assert verify_session_token(token, config) is True


def test_invalid_session_token_is_rejected():
    config = AuthConfig(password="correct-password", session_secret="secret")

    assert verify_session_token("not-a-valid-token", config) is False


def test_verify_session_token_rejects_missing_session_secret():
    config = AuthConfig(password="correct-password", session_secret="")

    assert verify_session_token("anything", config) is False


def test_create_session_token_rejects_missing_session_secret():
    config = AuthConfig(password="correct-password", session_secret="")

    with pytest.raises(RuntimeError, match="CAM_SESSION_SECRET"):
        create_session_token(config)


def test_session_token_signed_with_old_secret_is_rejected():
    original = AuthConfig(password="correct-password", session_secret="old-secret")
    rotated = AuthConfig(password="correct-password", session_secret="new-secret")

    token = create_session_token(original)

    assert verify_session_token(token, rotated) is False


def test_session_token_expires():
    config = AuthConfig(password="correct-password", session_secret="secret", max_age_seconds=-1)
    token = create_session_token(config)

    assert verify_session_token(token, config) is False


def test_cookie_name_is_stable():
    assert AUTH_COOKIE_NAME == "cam_session"
