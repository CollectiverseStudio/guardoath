"""Start-up work: an app builds its checkers once (the sensitive-data checker takes most of the time)."""
import guardoath.pii as pii
from guardoath.app import create_app


def test_create_app_builds_the_sensitive_data_checker_once(monkeypatch):
    for name in ("GUARDOATH_POLICY_FILE", "GUARDOATH_PACK_FILE", "GUARDOATH_API_KEY", "GUARDOATH_TENANT_STORE",
                 "GUARDOATH_DEMO_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GUARDOATH_ENABLE_PII", "1")
    original, calls = pii.build_pii_checker, []

    def counted():
        calls.append(1)
        return original()

    monkeypatch.setattr(pii, "build_pii_checker", counted)
    create_app()
    assert calls == [1]
