"""Integration acceptance for optional, owner-verified Cognito native MFA browser login."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from guardoath.admin_browser import BrowserAuthConfig, BoundExchange, create_browser_router
from guardoath.admin_console import VerifiedAdmin
from guardoath.admin_native_mfa import NativeMfaUnavailable


CONFIG = BrowserAuthConfig("sample.auth.us-east-1.amazoncognito.com",
                           "adminclient123", "https://guardoath.com/admin/login")
GOOD = "a" * 40
ORIGIN = {"Origin": "https://guardoath.com"}


class Native:
    def __init__(self, token=GOOD, unavailable=False):
        self.calls = []
        self.token = token
        self.unavailable = unavailable

    def begin(self, email, password):
        self.calls.append("begin")
        if self.unavailable:
            raise NativeMfaUnavailable("must never leak")
        return "t" * 60 if password == "long-test-password" else None

    def finish(self, ticket, otp):
        self.calls.append("finish")
        if self.unavailable:
            raise NativeMfaUnavailable("must never leak")
        return self.token if (ticket, otp) == ("t" * 60, "123456") else None


def client(native=None, verifier=None):
    app = FastAPI()
    app.include_router(create_browser_router(
        CONFIG, BoundExchange(CONFIG, sender=lambda *_: {"access_token": GOOD}),
        verifier or (lambda token: VerifiedAdmin("owner", frozenset({"owner"}), True)
                     if token == GOOD else None),
        native_auth=native,
    ))
    return TestClient(app, base_url="https://guardoath.com")


def test_native_flow_not_registered_without_explicit_opt_in():
    c = client()
    config = c.get("/v1/admin/auth/config")
    assert config.status_code == 200
    assert config.json() == {
        "domain": CONFIG.domain, "client_id": CONFIG.client_id,
        "redirect_uri": CONFIG.redirect_uri, "native_mfa_enabled": False,
        "native_enrollment_enabled": False,
    }
    assert config.headers["cache-control"] == "no-store"
    assert c.post("/v1/admin/auth/native/start", json={
        "email": "owner@example.com", "password": "long-test-password"}, headers=ORIGIN).status_code == 404
    assert 'id="native-auth" hidden' in c.get("/admin/login").text


def test_owner_mfa_challenge_is_required_before_releasing_signed_session():
    native = Native()
    c = client(native)
    config = c.get("/v1/admin/auth/config")
    assert config.status_code == 200
    assert config.json() == {
        "domain": CONFIG.domain, "client_id": CONFIG.client_id,
        "redirect_uri": CONFIG.redirect_uri, "native_mfa_enabled": True,
        "native_enrollment_enabled": False,
    }
    assert config.headers["cache-control"] == "no-store"
    assert native.calls == []
    assert GOOD not in config.text and "challenge_ticket" not in config.text
    assert "long-test-password" not in config.text
    page = c.get("/admin/login").text
    script = c.get("/admin/client.js").text
    assert 'id="native-password"' in page and 'id="native-code"' in page
    assert 'request("/v1/admin/auth/native/start"' in script
    assert 'request("/v1/admin/auth/native/finish"' in script
    assert 'el("native-password").value = "";' in script
    assert "nativeTicket = null;" in script
    assert 'sessionStorage.setItem("access_token"' not in script
    start = c.post("/v1/admin/auth/native/start", headers=ORIGIN,
                   json={"email": "owner@example.com", "password": "long-test-password"})
    assert start.status_code == 200 and start.json()["challenge"] == "software_token_mfa"
    assert GOOD not in start.text and "long-test-password" not in start.text
    finish = c.post("/v1/admin/auth/native/finish", headers=ORIGIN,
                    json={"ticket": start.json()["challenge_ticket"], "otp": "123456"})
    assert finish.status_code == 200 and finish.json()["access_token"] == GOOD
    assert finish.headers["cache-control"] == "no-store"
    assert native.calls == ["begin", "finish"]


def test_bad_otp_and_bad_password_do_not_issue_admin_access():
    c = client(Native())
    bad = c.post("/v1/admin/auth/native/start", headers=ORIGIN,
                 json={"email": "owner@example.com", "password": "wrong-password"})
    assert bad.status_code == 401 and "access_token" not in bad.text
    bad = c.post("/v1/admin/auth/native/finish", headers=ORIGIN,
                 json={"ticket": "wrong", "otp": "000000"})
    assert bad.status_code == 422 and "access_token" not in bad.text


def test_unauthorized_origins_are_denied_before_any_cognito_call():
    native = Native()
    c = client(native)
    for origin in (None, "https://other.example", "http://localhost:8000",
                   "https://guardoath.com,https://other.example"):
        h = {"Origin": origin} if origin is not None else {}
        for route, payload in [
                ("start", {"email": "owner@example.com", "password": "long-test-password"}),
                ("finish", {"ticket": "t" * 60, "otp": "123456"})]:
            result = c.post("/v1/admin/auth/native/" + route, headers=h, json=payload)
            assert result.status_code == 403
    assert native.calls == []


def test_missing_signed_recent_mfa_or_owner_group_is_fail_closed():
    c = client(Native(), verifier=lambda _: None)
    result = c.post("/v1/admin/auth/native/finish", headers=ORIGIN,
                    json={"ticket": "t" * 60, "otp": "123456"})
    assert result.status_code == 403
    assert "access_token" not in result.text


def test_provider_failure_never_leaks_password_or_provider_message():
    c = client(Native(unavailable=True))
    result = c.post("/v1/admin/auth/native/start", headers=ORIGIN,
                    json={"email": "owner@example.com", "password": "long-test-password"})
    assert result.status_code == 503
    assert "long-test-password" not in result.text
    assert "must never leak" not in result.text

