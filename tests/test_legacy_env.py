"""GuardOath must fail loudly on old environment names without leaking values."""
import pytest
from guardoath.app import create_app
from guardoath.legacy_env import reject_legacy_environment


def test_old_environment_names_refuse_startup_without_secret_disclosure(monkeypatch):
    monkeypatch.setenv("VALINATOR_ENABLE_PII", "sensitive-secret-value")
    monkeypatch.setenv("VALISENTRY_API_KEY", "another-private-value")
    with pytest.raises(ValueError) as exc:
        create_app()
    message = str(exc.value)
    assert "VALINATOR_ENABLE_PII -> GUARDOATH_ENABLE_PII" in message
    assert "VALISENTRY_API_KEY -> GUARDOATH_API_KEY" in message
    assert "sensitive-secret-value" not in message
    assert "another-private-value" not in message


def test_guardoath_environment_only_is_accepted(monkeypatch):
    monkeypatch.setenv("GUARDOATH_ENABLE_PII", "0")
    monkeypatch.setenv("GUARDOATH_API_KEY", "local-test-key")
    reject_legacy_environment()
    assert create_app() is not None
