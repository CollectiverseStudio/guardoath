"""Tenant sign-in (guardoath.tenant_sessions): sessions are checked locally (RS256 signature with the pool's
keys) and then with Cognito, and only a signed-in user can change a customer's settings and API keys.
Cognito is replaced by a fake; nothing leaves the test. The RSA test key is generated here, so no key is
stored in the repository."""
import base64
import hashlib
import json
import random
import urllib.error
import urllib.parse
import urllib.request

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("tldextract")
pytest.importorskip("phonenumbers")

from guardoath import tenant_sessions
from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.hosted import TenantService
from guardoath.tenant_record import TenantRecord, demo_key, demo_records, key_hash, new_key
from guardoath.tenant_sessions import (SESSION_HEADER, SHA256_DIGEST_INFO, UPSTREAM_PER_MINUTE, CognitoSessions,
                                       SignInBusy, parse_jwks, rs256_valid)
from guardoath.tenant_store import MemoryTenantStore

POOL, CLIENT, DOMAIN = "us-east-1_AbC123", "1a2b3c4d5e6f7g8h9i0j1k2l3m", "guardoath-sign-in.auth.us-east-1.amazoncognito.com"
REDIRECT = "https://guardoath.com/dashboard"
ISSUER = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
GETUSER_URL = "https://cognito-idp.us-east-1.amazonaws.com/"
NOW = 2_000_000_000
SECRET = "s" * 40
CHECKERS = default_checkers(enable_pii=True)
VERIFIER = "v" * 43


# A 2048-bit RSA test key, generated deterministically


def _probable_prime(value, rng):
    for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if value % small == 0:
            return value == small
    odd, twos = value - 1, 0
    while odd % 2 == 0:
        odd, twos = odd // 2, twos + 1
    for _ in range(24):
        x = pow(rng.randrange(2, value - 2), odd, value)
        if x in (1, value - 1):
            continue
        for _ in range(twos - 1):
            x = pow(x, 2, value)
            if x == value - 1:
                break
        else:
            return False
    return True


def _prime(bits, rng):
    while True:
        candidate = rng.getrandbits(bits) | (1 << (bits - 1)) | (1 << (bits - 2)) | 1
        if (candidate - 1) % 65537 and _probable_prime(candidate, rng):
            return candidate


def _key(seed):
    rng = random.Random(seed)
    p, q = _prime(1024, rng), _prime(1024, rng)
    while q == p:
        q = _prime(1024, rng)
    private = pow(65537, -1, (p - 1) * (q - 1))
    # (modulus, exponent, then the CRT parts used only for fast signing)
    return p * q, 65537, private % (p - 1), private % (q - 1), pow(q, -1, p), p, q


KEY = _key(20261102)
OTHER_KEY = _key(20261103)


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def b64_int(value):
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def sign(data, key=KEY):
    modulus, _, dp, dq, q_inverse, p, q = key
    size = (modulus.bit_length() + 7) // 8
    digest = SHA256_DIGEST_INFO + hashlib.sha256(data).digest()
    message = int.from_bytes(b"\x00\x01" + b"\xff" * (size - 3 - len(digest)) + b"\x00" + digest, "big")
    m1, m2 = pow(message, dp, p), pow(message, dq, q)
    return (m2 + q * ((q_inverse * (m1 - m2)) % p)).to_bytes(size, "big")


def jwk(key=KEY, kid="key-1", **changes):
    return {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": kid, "n": b64_int(key[0]), "e": b64_int(key[1]), **changes}


def token(kid="key-1", alg="RS256", key=KEY, signature=None, **changes):
    """A signed access token. With signature=, that signature is used instead (a forgery)."""
    payload = {"token_use": "access", "client_id": CLIENT, "iss": ISSUER, "exp": NOW + 3600,
               "scope": "openid email aws.cognito.signin.user.admin", "sub": "user-1", **changes}
    signing_input = b64(json.dumps({"kid": kid, "alg": alg}).encode()) + "." + b64(json.dumps(payload).encode())
    return signing_input + "." + b64(signature if signature is not None else sign(signing_input.encode(), key))


def random_signature(number):
    """256 bytes that look like an RS256 signature and are not one."""
    return random.Random(number).getrandbits(2048).to_bytes(256, "big")


def forged(value):
    head, payload, signature = value.split(".")
    raw = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    raw[-1] ^= 1
    return head + "." + payload + "." + b64(bytes(raw))


class FakeCognito:
    """The pool's keys, GetUser for the tokens it knows, and the token endpoint for one known code."""

    def __init__(self):
        self.users = {}            # token -> list of attributes
        self.codes = {}            # code -> access token
        self.jwks = {"keys": [jwk()]}
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        assert url == JWKS_URL
        return 200, json.dumps(self.jwks).encode()

    def post(self, url, headers, body):
        self.calls.append(url)
        if url == GETUSER_URL:
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

    def count(self, url):
        return self.calls.count(url)


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
    made = CognitoSessions(POOL, CLIENT, DOMAIN, REDIRECT, post=cognito.post, get=cognito.get,
                           clock=lambda: clock[0], wall=lambda: NOW)
    made.clock = clock
    return made


# The signature check


def test_the_signature_check_accepts_only_the_signed_bytes_and_key():
    data = b"header.payload"
    signature = sign(data)
    assert rs256_valid(data, signature, KEY[0], KEY[1])
    assert not rs256_valid(b"header.payloae", signature, KEY[0], KEY[1])
    assert not rs256_valid(data, signature, OTHER_KEY[0], OTHER_KEY[1])
    assert not rs256_valid(data, signature[:-1], KEY[0], KEY[1])
    assert not rs256_valid(data, (KEY[0] + 1).to_bytes(257, "big")[-256:], KEY[0], KEY[1])


def test_only_rsa_signing_keys_of_2048_bits_or_more_are_loaded():
    small_modulus = 3233  # 12 bits
    keys = parse_jwks(json.dumps({"keys": [
        jwk(kid="good"), jwk(kid="enc", use="enc"), jwk(kid="es", alg="ES256"), jwk(kid="ec", kty="EC"),
        {**jwk(kid="small"), "n": b64_int(small_modulus)}, {**jwk(kid="bad-n"), "n": "not base64!"}, {"kid": 7},
    ]}).encode())
    assert list(keys) == ["good"] and keys["good"] == (KEY[0], KEY[1])
    assert parse_jwks(b"not json") == {} and parse_jwks(b'{"keys": 7}') == {}


# Checking a session


def test_a_valid_session_names_its_tenant_and_is_checked_with_cognito_once_a_minute(sessions, cognito):
    value = tenant_user(cognito, "acme")
    assert sessions.tenant_for(value) == "acme" and sessions.tenant_for(value) == "acme"
    assert cognito.count(GETUSER_URL) == 1 and cognito.count(JWKS_URL) == 1
    sessions.clock[0] += 61
    assert sessions.tenant_for(value) == "acme" and cognito.count(GETUSER_URL) == 2
    assert cognito.count(JWKS_URL) == 1  # the keys are kept


def test_forged_tokens_are_refused_without_asking_cognito(sessions, cognito):
    for number in range(300):
        assert sessions.tenant_for(token(sub=f"user-{number}", signature=random_signature(number))) is None
    for number in range(3):  # a real signature with one bit changed
        assert sessions.tenant_for(forged(tenant_user(cognito, "acme", sub=f"user-{number}"))) is None
    assert sessions.tenant_for(token(key=OTHER_KEY)) is None
    assert cognito.count(GETUSER_URL) == 0 and cognito.count(JWKS_URL) == 1


def test_tokens_naming_unknown_keys_fetch_the_keys_at_most_once_per_five_minutes(sessions, cognito):
    for number in range(300):
        assert sessions.tenant_for(token(kid=f"unknown-{number}", signature=random_signature(number))) is None
    assert cognito.count(JWKS_URL) == 1 and cognito.count(GETUSER_URL) == 0
    sessions.clock[0] += 301
    assert sessions.tenant_for(token(kid="unknown-again")) is None and cognito.count(JWKS_URL) == 2


def test_a_rotated_key_is_used_after_the_next_refresh(sessions, cognito):
    assert sessions.tenant_for(tenant_user(cognito, "acme")) == "acme"
    cognito.jwks = {"keys": [jwk(), jwk(OTHER_KEY, kid="key-2")]}
    rotated = tenant_user(cognito, "acme", kid="key-2", key=OTHER_KEY, sub="user-2")
    assert sessions.tenant_for(rotated) is None  # keys were fetched less than five minutes ago
    sessions.clock[0] += 301
    assert sessions.tenant_for(rotated) == "acme"


def test_when_the_keys_cannot_be_fetched_every_session_is_refused(cognito):
    calls = []

    def failing(url):
        calls.append(url)
        raise OSError("no network")
    made = CognitoSessions(POOL, CLIENT, DOMAIN, REDIRECT, post=cognito.post, get=failing, clock=lambda: 0.0, wall=lambda: NOW)
    assert all(made.tenant_for(tenant_user(cognito, "acme", sub=f"u{n}")) is None for n in range(50))
    assert calls == [JWKS_URL] and cognito.count(GETUSER_URL) == 0


@pytest.mark.parametrize("changes", [
    {"client_id": "someoneelse"}, {"iss": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_Other"},
    {"token_use": "id"}, {"exp": NOW}, {"exp": "later"}, {"scope": "openid email"},
])
def test_a_signed_token_of_another_pool_client_kind_or_time_is_refused_without_asking_cognito(sessions, cognito, changes):
    value = tenant_user(cognito, "acme", **changes)
    assert sessions.tenant_for(value) is None and cognito.count(GETUSER_URL) == 0


@pytest.mark.parametrize("alg", ["none", "HS256", "RS512", "rs256"])
def test_only_rs256_is_accepted(sessions, cognito, alg):
    assert sessions.tenant_for(tenant_user(cognito, "acme", alg=alg)) is None and cognito.count(GETUSER_URL) == 0


@pytest.mark.parametrize("value", ["", "not-a-token", "a.b", "a.b.c.d", "a.b.c", "x" * 9000, None, 7, "a b.c.d"])
def test_malformed_tokens_are_refused_without_any_network_call(sessions, cognito, value):
    assert sessions.tenant_for(value) is None and cognito.calls == []


def test_a_revoked_token_is_asked_about_once_a_minute_at_most(sessions, cognito):
    revoked = token(sub="revoked")  # signed, but Cognito refuses it
    assert all(sessions.tenant_for(revoked) is None for _ in range(100))
    assert cognito.count(GETUSER_URL) == 1
    sessions.clock[0] += 61
    assert sessions.tenant_for(revoked) is None and cognito.count(GETUSER_URL) == 2


def test_cognito_is_asked_at_most_60_times_a_minute_and_over_that_the_check_is_refused(sessions, cognito):
    users = [tenant_user(cognito, "acme", sub=f"user-{n}") for n in range(UPSTREAM_PER_MINUTE + 10)]
    allowed = [sessions.tenant_for(value) for value in users[:UPSTREAM_PER_MINUTE]]
    assert allowed == ["acme"] * UPSTREAM_PER_MINUTE
    for value in users[UPSTREAM_PER_MINUTE:]:
        with pytest.raises(SignInBusy):
            sessions.tenant_for(value)
    assert cognito.count(GETUSER_URL) == UPSTREAM_PER_MINUTE
    assert sessions.tenant_for(users[0]) == "acme"  # a cached session is not affected
    sessions.clock[0] += 60
    assert sessions.tenant_for(users[-1]) == "acme"


def test_cognito_is_asked_at_most_4_times_at_once(sessions, cognito):
    for _ in range(4):
        sessions._upstream()
    with pytest.raises(SignInBusy):
        sessions.tenant_for(tenant_user(cognito, "acme"))
    for _ in range(4):
        sessions._at_once.release()
    assert sessions.tenant_for(tenant_user(cognito, "acme")) == "acme"


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
    cognito.codes["code-3"] = forged(value)
    assert sessions.exchange("code-3", VERIFIER) is None
    for code, verifier in [("bad code!", VERIFIER), ("code-1", "short"), ("code-1", "v" * 129)]:
        assert sessions.exchange(code, verifier) is None


def test_trading_codes_counts_against_the_same_limit(sessions, cognito):
    for number in range(UPSTREAM_PER_MINUTE):
        assert sessions.exchange(f"unknown-{number}", VERIFIER) is None
    with pytest.raises(SignInBusy):
        sessions.exchange("one-more", VERIFIER)
    assert cognito.count(f"https://{DOMAIN}/oauth2/token") == UPSTREAM_PER_MINUTE


def test_the_public_configuration_has_no_secret(sessions):
    assert sessions.public_config() == {
        "authorize_url": f"https://{DOMAIN}/oauth2/authorize", "logout_url": f"https://{DOMAIN}/logout",
        "client_id": CLIENT, "redirect_uri": REDIRECT, "scope": "openid email aws.cognito.signin.user.admin"}


@pytest.mark.parametrize("args", [
    ("not-a-pool", CLIENT, DOMAIN, REDIRECT), (POOL, "Has Spaces", DOMAIN, REDIRECT),
    (POOL, CLIENT, "https://" + DOMAIN, REDIRECT), (POOL, CLIENT, "guardoath.com", REDIRECT),
    (POOL, CLIENT, "guardoath-sign-in.auth.us-west-2.amazoncognito.com", REDIRECT),
    (POOL, CLIENT, "x.auth.us-east-1.amazoncognito.com.evil.example", REDIRECT),
    (POOL, CLIENT, DOMAIN, "https://guardoath.com/elsewhere"), (POOL, CLIENT, DOMAIN, "http://guardoath.com/dashboard"),
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
    made = CognitoSessions.from_environment()
    assert made.issuer == ISSUER and made.jwks_url == JWKS_URL


def test_redirects_are_not_followed_and_only_https_is_used():
    handler = tenant_sessions._NoRedirects()
    request = urllib.request.Request("https://cognito-idp.us-east-1.amazonaws.com/", method="POST")
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, None, 302, "Found", {}, "https://elsewhere.example/")
    with pytest.raises(ValueError, match="Only https"):
        tenant_sessions.https_get("http://cognito-idp.us-east-1.amazonaws.com/")


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
    client = site[0]
    assert change(client, keyed(demo_key("demo1", SECRET)), 1, {"check_pii": "flag"}).status_code == 200


def test_many_forged_sessions_on_get_and_post_are_refused_without_asking_cognito(site, cognito):
    client, tenants, _, session = site
    before = tenants.store.get("acme")
    expected = {"detail": "Sign-in expired or not valid; sign in again"}
    for number in range(100):
        fake = token(sub=f"attacker-{number}", signature=random_signature(number))
        for response in (client.get("/v1/applications/current", headers=signed(fake)),
                         client.get("/v1/tenants/current/keys", headers=signed(fake)),
                         change(client, signed(fake), 1, {"check_pii": "block"})):
            assert response.status_code == 401 and response.json() == expected
    assert cognito.count(GETUSER_URL) == 0 and cognito.count(JWKS_URL) <= 1
    assert tenants.store.get("acme") == before


def test_a_revoked_session_is_refused_on_get_and_post_and_cognito_is_asked_once(site, cognito):
    client = site[0]
    revoked = token(sub="revoked")
    for _ in range(30):
        assert client.get("/v1/applications/current", headers=signed(revoked)).status_code == 401
        assert change(client, signed(revoked), 1, {"check_pii": "block"}).status_code == 401
    assert cognito.count(GETUSER_URL) == 1


def test_over_the_limit_a_session_check_is_refused_with_503(site, cognito):
    client = site[0]
    users = [tenant_user(cognito, "acme", sub=f"user-{n}") for n in range(UPSTREAM_PER_MINUTE + 1)]
    for value in users[:UPSTREAM_PER_MINUTE]:
        assert client.get("/v1/applications/current", headers=signed(value)).status_code == 200
    busy = client.get("/v1/applications/current", headers=signed(users[-1]))
    assert busy.status_code == 503 and busy.headers["retry-after"] == "5"
    assert busy.json() == {"detail": "Sign-in checks are busy; try again shortly"}


@pytest.mark.parametrize("value", ["not-a-token", token(sub="nobody")])
def test_an_invalid_session_is_refused(site, value):
    response = site[0].get("/v1/applications/current", headers=signed(value))
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
    assert client.post("/v1/tenants/current/keys", headers=signed(session), json={}).status_code == 409
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
