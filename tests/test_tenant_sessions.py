"""Tenant sign-in (guardoath.tenant_sessions): sessions are checked with Cognito, and only a signed-in user
can change a customer's settings and API keys. Cognito is replaced by a fake; nothing leaves the test."""
import base64
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, demo_records, key_hash, new_key
from guardoath.tenant_sessions import SESSION_HEADER, CognitoSessions, claims
from guardoath.tenant_store import MemoryTenantStore

POOL, CLIENT, DOMAIN = "us-east-1_AbC123", "1a2b3c4d5e6f7g8h9i0j1k2l3m", "guardoath-sign-in.auth.us-east-1.amazoncognito.com"
REDIRECT = "https://guardoath.com/dashboard"
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}"
NOW = 2_000_000_000
SECRET = "s" * 40
CHECKERS = default_checkers(enable_pii=True)
VERIFIER = "v" * 43


def part(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def token(**changes):
    payload = {"token_use": "access", "client_id": CLIENT, "iss": ISSUER, "exp": NOW + 3600,
               "scope": "openid email aws.cognito.signin.user.admin", "sub": "user-1", **changes}
    return part({"alg": "RS256"}) + "." + part(payload) + ".c2lnbmF0dXJl"


class FakeCognito:
    """GetUser answers for the tokens it knows; the token endpoint trades one known code."""

    def __init__(self):
        self.users = {}            # token -> list of attributes
        self.codes = {}            # code -> access token
        self.calls = []

    def __call__(self, url, headers, body):
        self.calls.append(url)
        if url == "https://cognito-idp.us-east-1.amazonaws.com/":
            assert headers["X-Amz-Target"] == "AWSCognitoIdentityProviderService.GetUser"
            attributes = self.users.get(json.loads(body)["AccessToken"])
            if attributes is None:
                return 400, b'{"__type": "NotAuthorizedException"}'
            return 200, json.dumps({"Username": "user-1", "UserAttributes": attributes}).encode()
        if url == f"https://{DOMAIN}/oauth2/token":
            form = dict(urllib.parse.parse_qsl(body.decode()))
            assert form["grant_type"] == "authorization_code" and form["client_id"] == CLIENT
            assert form["redirect_uri"] == REDIRECT and form["code_verifier"] == VERIFIER
            access = self.codes.get(form["code"])
            if access is None:
                return 400, b'{"error": "invalid_grant"}'
            return 200, json.dumps({"access_token": access, "expires_in": 3600, "token_type": "Bearer"}).encode()
        raise AssertionError("unexpected URL " + url)


def tenant_user(cognito, tenant_id, **changes):
    value = token(**changes)
    cognito.users[value] = [{"Name": "email", "Value": "owner@example.com"}, {"Name": "custom:tenant_id", "Value": tenant_id}]
    return value


@pytest.fixture
def cognito():
    return FakeCognito()


@pytest.fixture
def sessions(cognito):
    clock = [0.0]
    made = CognitoSessions(POOL, CLIENT, DOMAIN, REDIRECT, post=cognito, clock=lambda: clock[0], wall=lambda: NOW)
    made.clock = clock
    return made


# Checking a session


def test_a_valid_session_names_its_tenant_and_is_checked_with_cognito_once_a_minute(sessions, cognito):
    value = tenant_user(cognito, "acme")
    assert sessions.tenant_for(value) == "acme" and sessions.tenant_for(value) == "acme"
    assert len(cognito.calls) == 1
    sessions.clock[0] += 61
    assert sessions.tenant_for(value) == "acme" and len(cognito.calls) == 2


@pytest.mark.parametrize("changes", [
    {"client_id": "someoneelse"}, {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"},
    {"token_use": "id"}, {"exp": NOW}, {"exp": "later"}, {"scope": "openid email"},
])
def test_a_token_of_another_pool_client_kind_or_time_is_refused_without_asking_cognito(sessions, cognito, changes):
    value = tenant_user(cognito, "acme", **changes)
    assert sessions.tenant_for(value) is None and cognito.calls == []


@pytest.mark.parametrize("value", ["", "not-a-token", "a.b", "a.b.c.d", "x" * 9000, None, 7])
def test_malformed_tokens_are_refused(sessions, cognito, value):
    assert sessions.tenant_for(value) is None and cognito.calls == []
    assert claims(value) is None


def test_a_token_cognito_refuses_is_refused(sessions, cognito):
    assert sessions.tenant_for(token()) is None and len(cognito.calls) == 1


@pytest.mark.parametrize("attributes", [
    [{"Name": "email", "Value": "owner@example.com"}],
    [{"Name": "custom:tenant_id", "Value": "acme"}, {"Name": "custom:tenant_id", "Value": "other"}],
    [{"Name": "custom:tenant_id", "Value": "Not A Tenant"}],
    [{"Name": "custom:tenant_id", "Value": 7}],
])
def test_a_user_without_exactly_one_valid_tenant_is_refused(sessions, cognito, attributes):
    value = token()
    cognito.users[value] = attributes
    assert sessions.tenant_for(value) is None


def test_the_sign_in_code_is_traded_for_a_checked_session(sessions, cognito):
    value = tenant_user(cognito, "acme")
    cognito.codes["code-1"] = value
    assert sessions.exchange("code-1", VERIFIER) == {"access_token": value, "expires_in": 3600}
    assert sessions.exchange("code-2", VERIFIER) is None
    cognito.codes["code-3"] = token(sub="nobody")
    assert sessions.exchange("code-3", VERIFIER) is None
    for code, verifier in [("bad code!", VERIFIER), ("code-1", "short"), ("code-1", "v" * 129)]:
        assert sessions.exchange(code, verifier) is None


def test_the_public_configuration_has_no_secret(sessions):
    assert sessions.public_config() == {
        "authorize_url": f"https://{DOMAIN}/oauth2/authorize", "logout_url": f"https://{DOMAIN}/logout",
        "client_id": CLIENT, "redirect_uri": REDIRECT, "scope": "openid email aws.cognito.signin.user.admin"}


@pytest.mark.parametrize("args", [
    ("not-a-pool", CLIENT, DOMAIN, REDIRECT), (POOL, "Has Spaces", DOMAIN, REDIRECT),
    (POOL, CLIENT, "https://" + DOMAIN, REDIRECT), (POOL, CLIENT, DOMAIN, "https://guardoath.com/elsewhere"),
    (POOL, CLIENT, DOMAIN, "http://guardoath.com/dashboard"),
])
def test_bad_settings_stop_startup(args):
    with pytest.raises(ValueError):
        CognitoSessions(*args)


def test_sign_in_settings_come_all_together_or_not_at_all(monkeypatch):
    names = ("GUARDOATH_COGNITO_POOL_ID", "GUARDOATH_COGNITO_CLIENT_ID", "GUARDOATH_COGNITO_DOMAIN", "GUARDOATH_COGNITO_REDIRECT_URI")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    assert CognitoSessions.from_environment() is None
    monkeypatch.setenv("GUARDOATH_COGNITO_POOL_ID", POOL)
    with pytest.raises(ValueError, match="needs all of"):
        CognitoSessions.from_environment()
    for name, value in zip(names, (POOL, CLIENT, DOMAIN, REDIRECT)):
        monkeypatch.setenv(name, value)
    assert CognitoSessions.from_environment().issuer == ISSUER


# The API


@pytest.fixture
def site(sessions, cognito):
    key, digest = new_key("acme")
    customer = TenantRecord(tenant_id="acme", display_name="Acme", key_hashes=(digest,), check_secrets="flag",
                            monthly_allowance=100)
    tenants = TenantService(MemoryTenantStore((*demo_records(key_secret=SECRET), customer)), CHECKERS, SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants, sessions=sessions))
    return client, tenants, key, tenant_user(cognito, "acme")


def signed(value):
    return {SESSION_HEADER: value}


def keyed(value):
    return {"X-GuardOath-Key": value}


def change(client, headers, revision, changes):
    return client.post("/v1/tenants/current/checks", headers=headers, json={"revision": revision, "changes": changes})


def test_a_signed_in_user_changes_the_checks_and_the_api_key_cannot(site):
    client, tenants, key, session = site
    refused = change(client, keyed(key), 1, {"check_text_security": "block"})
    assert refused.status_code == 403 and "Sign in" in refused.json()["detail"]
    saved = change(client, signed(session), 1, {"check_text_security": "block"})
    assert saved.status_code == 200 and saved.json()["checks"]["text_security"] == "block"
    assert tenants.store.get("acme").check_text_security == "block"


def test_a_signed_in_user_sees_and_runs_the_records_checks(site):
    client, tenants, _, session = site
    data = client.get("/v1/applications/current", headers=signed(session)).json()
    assert data["application_id"] == "acme" and [p["version"] for p in data["policies"]] == ["tenant"]
    result = client.post("/v1/checks/input", headers=signed(session), json={"application_id": "acme", "content": "Hello"})
    assert result.status_code == 200 and tenants.store.get("acme").used(data["tenant"]["month"]) == 1


def test_demo_users_keep_changing_their_own_record_with_their_demo_key(site):
    client, tenants, _, _ = site
    token = demo_key("demo1", SECRET)
    assert change(client, keyed(token), 1, {"check_pii": "flag"}).status_code == 200


@pytest.mark.parametrize("value", ["not-a-token", token(sub="nobody")])
def test_an_invalid_session_is_refused(site, value):
    client = site[0]
    response = client.get("/v1/applications/current", headers=signed(value))
    assert response.status_code == 401 and response.json() == {"detail": "Sign-in expired or not valid; sign in again"}


def test_a_session_naming_a_demo_user_or_an_unknown_tenant_is_refused(site, cognito):
    client = site[0]
    for tenant_id in ("demo1", "nobody"):
        value = tenant_user(cognito, tenant_id, sub="user-" + tenant_id)
        assert client.get("/v1/applications/current", headers=signed(value)).status_code == 401


def test_a_disabled_tenant_cannot_sign_in(site):
    client, tenants, _, session = site
    record = tenants.store.get("acme")
    tenants.store._records["acme"] = TenantRecord.model_validate({**record.model_dump(), "status": "disabled"})
    assert client.get("/v1/applications/current", headers=signed(session)).status_code == 401


def test_a_signed_in_user_creates_and_revokes_api_keys(site):
    client, tenants, key, session = site
    listed = client.get("/v1/tenants/current/keys", headers=signed(session)).json()
    assert listed == {"keys": [{"key_id": key_hash(key)[:12]}], "max_keys": 2}
    created = client.post("/v1/tenants/current/keys", headers=signed(session), json={})
    assert created.status_code == 200 and created.headers["cache-control"] == "no-store"
    fresh = created.json()["api_key"]
    assert fresh.startswith("gok_acme_") and fresh not in tenants.store.get("acme").model_dump_json()
    assert len(created.json()["keys"]) == 2
    full = client.post("/v1/tenants/current/keys", headers=signed(session), json={})
    assert full.status_code == 409
    body = {"application_id": "acme", "content": "Hello"}
    assert client.post("/v1/checks/input", headers=keyed(fresh), json=body).status_code == 200
    revoked = client.post("/v1/tenants/current/keys/revoke", headers=signed(session), json={"key_id": key_hash(key)[:12]})
    assert revoked.status_code == 200 and revoked.json()["keys"] == [{"key_id": key_hash(fresh)[:12]}]
    assert client.post("/v1/checks/input", headers=keyed(key), json=body).status_code == 401
    assert client.post("/v1/checks/input", headers=keyed(fresh), json=body).status_code == 200
    missing = client.post("/v1/tenants/current/keys/revoke", headers=signed(session), json={"key_id": "0" * 12})
    assert missing.status_code == 404


def test_api_keys_cannot_manage_api_keys(site):
    client, _, key, _ = site
    assert client.get("/v1/tenants/current/keys", headers=keyed(key)).status_code == 403
    assert client.post("/v1/tenants/current/keys", headers=keyed(key), json={}).status_code == 403
    assert client.post("/v1/tenants/current/keys", headers=keyed(demo_key("demo1", SECRET)), json={}).status_code == 403


def test_sign_in_endpoints(site, cognito):
    client, _, _, session = site
    config = client.get("/v1/sign-in")
    assert config.status_code == 200 and config.json()["client_id"] == CLIENT
    cognito.codes["code-1"] = session
    traded = client.post("/v1/sign-in/token", json={"code": "code-1", "code_verifier": VERIFIER})
    assert traded.status_code == 200 and traded.json()["access_token"] == session
    assert traded.headers["cache-control"] == "no-store"
    failed = client.post("/v1/sign-in/token", json={"code": "code-9", "code_verifier": VERIFIER})
    assert failed.status_code == 401 and failed.json() == {"detail": "Sign-in failed; try again"}


def test_without_sign_in_configured_the_endpoints_say_so_and_sessions_are_refused():
    tenants = TenantService(MemoryTenantStore(demo_records(key_secret=SECRET)), CHECKERS, SECRET)
    client = TestClient(create_app(engine=demo_engine(), credentials={}, tenants=tenants))
    assert client.get("/v1/sign-in").status_code == 404
    assert client.post("/v1/sign-in/token", json={"code": "c", "code_verifier": VERIFIER}).status_code == 404
    assert client.get("/v1/applications/current", headers=signed(token())).status_code == 401


def test_sign_in_needs_tenant_records(sessions):
    with pytest.raises(ValueError, match="needs tenant records"):
        create_app(engine=demo_engine(), credentials={"k": "demo"}, sessions=sessions)
