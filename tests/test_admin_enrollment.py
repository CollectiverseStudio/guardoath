"""Real signed-token enrollment acceptance; every provider/storage call is local.

Fresh RSA keys are generated in the test process. No live Cognito account,
bearer token, OTP seed or AWS credential is needed or stored in the repository.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier, Lock

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from guardoath.admin_audit import AdminAuditReader
from guardoath.admin_browser import BrowserAuthConfig, BoundExchange, create_browser_router
from guardoath.admin_cognito import verify_admin
from guardoath.admin_enrollment import (
    CognitoNativeEnrollment, EnrollmentCodeMismatch, EnrollmentUnavailable,
)
from guardoath.admin_enrollment_state import DynamoEnrollmentState, EnrollmentStateUnavailable
from guardoath.admin_native_mfa import CognitoNativeMfa
from guardoath.tenant_sessions import CognitoSessions

POOL, CLIENT = "us-east-1_Synthetic", "syntheticadminclient"
DOMAIN = "synthetic.auth.us-east-1.amazoncognito.com"
SUB = "abcdefab-cdef-abcd-efab-cdefabcdefab"
EMAIL, PASSWORD = "owner@example.invalid", "synthetic-password-for-tests"
SEED = "A" * 32
MASTER = b"k" * 32
ORIGIN = {"Origin": "https://guardoath.com"}


class ProviderError(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code, "Message": "private-provider-detail"}}
        super().__init__("private-provider-detail")


class FakeDDB:
    """Atomic, strongly consistent fake with before/after response-loss injection."""
    def __init__(self):
        self.items, self.calls = {}, []
        self.lock = Lock()
        self.fail_write = None
        self.fail_read = False

    def get_item(self, **params):
        self.calls.append(("get", deepcopy(params)))
        assert params["TableName"] == "independent-audit"
        assert params["ConsistentRead"] is True
        if self.fail_read:
            raise ProviderError("InternalServerError")
        with self.lock:
            item = self.items.get(params["Key"]["event_id"]["S"])
            return {"Item": deepcopy(item)} if item is not None else {}

    def put_item(self, **params):
        self.calls.append(("put", deepcopy(params)))
        assert params["TableName"] == "independent-audit"
        item = deepcopy(params["Item"])
        key = item["event_id"]["S"]
        control = json.loads(item["enrollment_control"]["S"])
        loss = self.fail_write(control) if self.fail_write else None
        with self.lock:
            if loss == "before":
                raise ProviderError("InternalServerError")
            previous = self.items.get(key)
            if params["ConditionExpression"] == "attribute_not_exists(event_id)":
                allowed = previous is None
            else:
                assert params["ConditionExpression"] == "attribute_exists(event_id) AND #control = :original"
                assert params["ExpressionAttributeNames"] == {"#control": "enrollment_control"}
                allowed = previous is not None and previous.get("enrollment_control") == params["ExpressionAttributeValues"][":original"]
            if not allowed:
                raise ProviderError("ConditionalCheckFailedException")
            self.items[key] = item
            if loss == "after":
                raise ProviderError("InternalServerError")
        return {}

    def transact_write_items(self, **params):
        raise AssertionError("Enrollment must not mutate tenant records or audit receipts")


class Cognito:
    def __init__(self):
        self.calls, self.tokens = [], set()
        self.auth = {}
        self.current = self.admin = None
        self.enabled, self.status, self.groups = True, "CONFIRMED", [{"GroupName": "GuardOathOwners"}]
        self.factors, self.preferred = [], ""
        self.revoked = False
        self.fail = {}
        self.hooks = {}
        self.verify_status = "SUCCESS"
        self.preference_persisted = True

    def call(self, name):
        self.calls.append(name)
        if name in self.hooks:
            self.hooks[name]()
        if name in self.fail:
            raise ProviderError(self.fail[name])

    def identity(self):
        return {"Username": "synthetic-owner", "UserAttributes": [
            {"Name": "sub", "Value": SUB}, {"Name": "email", "Value": EMAIL},
            {"Name": "email_verified", "Value": "true"}],
            "UserMFASettingList": deepcopy(self.factors), "PreferredMfaSetting": self.preferred}

    def initiate_auth(self, **params):
        self.call("password")
        assert params["ClientId"] == CLIENT and params["AuthFlow"] == "USER_PASSWORD_AUTH"
        if params["AuthParameters"] != {"USERNAME": EMAIL, "PASSWORD": PASSWORD}:
            raise ProviderError("NotAuthorizedException")
        return deepcopy(self.auth)

    def get_user(self, **params):
        self.call("live")
        if self.revoked or params["AccessToken"] not in self.tokens:
            raise ProviderError("NotAuthorizedException")
        return deepcopy(self.current) if self.current is not None else self.identity()

    def admin_get_user(self, **params):
        self.call("account")
        assert params["UserPoolId"] == POOL
        if params["Username"] != "synthetic-owner":
            raise ProviderError("UserNotFoundException")
        value = self.identity() if self.admin is None else deepcopy(self.admin)
        return {"Enabled": self.enabled, "UserStatus": self.status, **value}

    def admin_list_groups_for_user(self, **params):
        self.call("groups")
        assert params == {"UserPoolId": POOL, "Username": "synthetic-owner", "Limit": 60}
        return deepcopy(self.groups) if isinstance(self.groups, dict) else {"Groups": deepcopy(self.groups)}

    def associate_software_token(self, **params):
        self.call("associate")
        assert params["AccessToken"] in self.tokens
        assert self.factors == []
        assert set(params) == {"AccessToken"}
        # This is the real documented AccessToken response shape: no Session.
        return {"SecretCode": SEED}

    def verify_software_token(self, **params):
        self.call("verify")
        assert set(params) == {"AccessToken", "UserCode"}
        assert params["AccessToken"] in self.tokens
        if params["UserCode"] != "123456":
            raise ProviderError("CodeMismatchException")
        if self.verify_status == "SUCCESS":
            self.factors = ["SOFTWARE_TOKEN_MFA"]
        return {"Status": self.verify_status}

    def set_user_mfa_preference(self, **params):
        self.call("prefer")
        assert params["AccessToken"] in self.tokens
        assert params["SoftwareTokenMfaSettings"] == {"Enabled": True, "PreferredMfa": True}
        if self.preference_persisted:
            self.preferred = "SOFTWARE_TOKEN_MFA"
        return {}

    def respond_to_auth_challenge(self, **params):
        raise AssertionError("Enrollment must never create an administrator session")


def b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Harness:
    def __init__(self, key):
        self.key, self.now = key, [2_000_000_000]
        self.db, self.provider = FakeDDB(), Cognito()
        public = key.public_key().public_numbers()
        number = lambda v: b64(v.to_bytes((v.bit_length() + 7) // 8, "big"))
        jwks = json.dumps({"keys": [{"kid": "synthetic", "kty": "RSA", "alg": "RS256",
                                   "use": "sig", "n": number(public.n), "e": number(public.e)}]}).encode()
        self.sessions = CognitoSessions(POOL, CLIENT, DOMAIN, "https://guardoath.com/dashboard",
                                       get=lambda _: (200, jwks), wall=lambda: self.now[0])
        self.service = self.another()
        self.issue()

    def another(self):
        state = DynamoEnrollmentState(self.db, "independent-audit", MASTER, POOL, CLIENT)
        return CognitoNativeEnrollment(self.provider, self.sessions, state, MASTER,
                                       enrollment_subject=SUB,
                                       wallclock=lambda: self.now[0], clock=lambda: self.now[0])

    def issue(self, *, header=None, **claims):
        payload = {"iss": self.sessions.issuer, "client_id": CLIENT, "token_use": "access",
                   "scope": "aws.cognito.signin.user.admin", "exp": self.now[0] + 3600,
                   "sub": SUB, "username": "synthetic-owner", "auth_time": self.now[0],
                   "amr": ["pwd"], **claims}
        raw = b64(json.dumps({"kid": "synthetic", "alg": "RS256", **(header or {})}).encode()) + "." + b64(json.dumps(payload).encode())
        self.token = raw + "." + b64(self.key.sign(raw.encode(), padding.PKCS1v15(), hashes.SHA256()))
        self.provider.tokens.add(self.token)
        self.provider.auth = {"AuthenticationResult": {"AccessToken": self.token, "RefreshToken": "synthetic-refresh-not-returned"}}
        return self.token

    def begin(self):
        return self.service.begin(EMAIL, PASSWORD)

    def record(self):
        return self.service._state.read(SUB)

    def client(self):
        config = BrowserAuthConfig(DOMAIN, CLIENT, "https://guardoath.com/admin/login")
        app = FastAPI()
        native = CognitoNativeMfa(self.provider, CLIENT, MASTER, wallclock=lambda: self.now[0])
        app.include_router(create_browser_router(config, BoundExchange(config),
            lambda value: verify_admin(value, self.sessions, self.provider),
            native_auth=native, native_enrollment=self.service))
        return TestClient(app, base_url="https://guardoath.com")


@pytest.fixture
def h(rsa_key):
    return Harness(rsa_key)


def test_real_signed_bootstrap_enrolls_but_never_authorizes_admin(h, monkeypatch):
    monkeypatch.setattr("guardoath.admin_cognito.time", lambda: h.now[0])
    result = h.begin()
    assert set(result) == {"setup_key", "enrollment_ticket", "expires_in"}
    assert result["setup_key"] == SEED and result["expires_in"] == 180
    assert h.record()["phase"] == "READY" and h.record()["state"] == "PENDING"
    assert h.service.finish(result["enrollment_ticket"], "123456") is True
    assert h.record()["state"] == "COMPLETE"
    assert h.provider.factors == ["SOFTWARE_TOKEN_MFA"] and h.provider.preferred == "SOFTWARE_TOKEN_MFA"
    assert h.provider.calls.count("associate") == h.provider.calls.count("verify") == h.provider.calls.count("prefer") == 1
    assert verify_admin(h.token, h.sessions, h.provider) is None  # still signed amr=[pwd]
    assert h.service.finish(result["enrollment_ticket"], "123456") is False
    assert h.begin() is None
    saved = json.dumps(h.db.items)
    assert all(value not in saved for value in (SEED, h.token, EMAIL, PASSWORD, "123456"))
    assert all(set(item) == {"event_id", "enrollment_control"} for item in h.db.items.values())


@pytest.mark.parametrize("changes", [
    {"client_id": "customerclient"}, {"iss": "https://wrong-pool.invalid"},
    {"token_use": "id"}, {"scope": "openid email"}, {"exp": 1},
    {"auth_time": None}, {"auth_time": True}, {"auth_time": "2000000000"},
    {"auth_time": 1_999_999_819}, {"auth_time": 2_000_000_031},
    {"sub": "not-a-subject"}, {"username": "unbound-owner"},
])
def test_signed_claims_cannot_cross_the_bootstrap_boundary(h, changes):
    h.issue(**changes)
    assert h.begin() is None
    assert "associate" not in h.provider.calls and not h.db.items


@pytest.mark.parametrize("shape", ["unsigned", "wrong_algorithm", "signature", "challenge", "id_token_only", "nonobject"])
def test_untrusted_password_result_denied_before_provider_mutation(h, shape):
    if shape == "wrong_algorithm":
        h.issue(header={"alg": "HS256"})
    elif shape == "signature":
        parts = h.token.split(".")
        signature = bytearray(base64.urlsafe_b64decode(parts[2] + "=" * (-len(parts[2]) % 4)))
        signature[0] ^= 1
        h.provider.auth["AuthenticationResult"]["AccessToken"] = ".".join(parts[:2]) + "." + b64(signature)
    elif shape == "unsigned":
        h.provider.auth["AuthenticationResult"]["AccessToken"] = "not-an-authorized-bearer-token"
    elif shape == "challenge":
        h.provider.auth["ChallengeName"] = "SOFTWARE_TOKEN_MFA"
    elif shape == "id_token_only":
        h.provider.auth = {"AuthenticationResult": {"IdToken": h.token}}
    else:
        h.provider.auth = []
    assert h.begin() is None
    assert h.provider.calls == ["password"]


@pytest.mark.parametrize("problem", [
    "nonowner", "disabled", "unconfirmed", "external", "revoked", "email_false", "email_missing",
    "email_mismatch", "subject_mismatch", "duplicate", "current_username", "admin_username",
    "paged_groups", "malformed_groups", "totp", "sms", "preferred", "null_factors", "string_factors",
    "legacy_sms", "null_legacy", "null_preferred",
])
def test_only_a_live_verified_owner_with_no_factor_can_associate(h, problem):
    p = h.provider
    if problem == "nonowner": p.groups = [{"GroupName": "Customers"}]
    elif problem == "disabled": p.enabled = False
    elif problem == "unconfirmed": p.status = "UNCONFIRMED"
    elif problem == "external": p.status = "EXTERNAL_PROVIDER"
    elif problem == "revoked": p.revoked = True
    elif problem == "totp": p.factors = ["SOFTWARE_TOKEN_MFA"]
    elif problem == "sms": p.factors = ["SMS_MFA"]
    elif problem == "preferred": p.preferred = "SOFTWARE_TOKEN_MFA"
    elif problem == "null_factors": p.factors = None
    elif problem == "string_factors": p.factors = "SOFTWARE_TOKEN_MFA"
    elif problem == "null_preferred": p.preferred = None
    elif problem == "paged_groups": p.groups = {"Groups": p.groups, "NextToken": "uninspected"}
    elif problem == "malformed_groups": p.groups = [{"GroupName": "GuardOathOwners"}, None]
    else:
        p.current = p.identity()
        if problem == "email_false": p.current["UserAttributes"][2]["Value"] = "false"
        elif problem == "email_missing": p.current["UserAttributes"].pop(2)
        elif problem == "email_mismatch": p.current["UserAttributes"][1]["Value"] = "other@example.invalid"
        elif problem == "subject_mismatch": p.current["UserAttributes"][0]["Value"] = "00000000-0000-0000-0000-000000000000"
        elif problem == "duplicate": p.current["UserAttributes"].append(deepcopy(p.current["UserAttributes"][2]))
        elif problem == "current_username": p.current["Username"] = "other"
        elif problem == "admin_username":
            p.admin = p.identity(); p.admin["Username"] = "other"
        elif problem == "legacy_sms": p.current["MFAOptions"] = [{"DeliveryMedium": "SMS", "AttributeName": "phone_number"}]
        elif problem == "null_legacy": p.current["MFAOptions"] = None
    assert h.begin() is None
    assert "associate" not in p.calls and not h.db.items


def test_bootstrap_rereads_live_owner_after_claim_before_association(h):
    count = [0]
    def remove_owner():
        count[0] += 1
        if count[0] == 2:
            h.provider.groups = []
    h.provider.hooks["groups"] = remove_owner
    assert h.begin() is None
    assert "associate" not in h.provider.calls
    assert h.record()["state"] == "BLOCKED"


def test_ticket_is_confidential_randomized_and_not_cross_client_or_key(h):
    result = h.begin()
    ticket = result["enrollment_ticket"]
    raw = base64.urlsafe_b64decode(ticket[3:] + "=" * (-len(ticket[3:]) % 4))
    assert all(value.encode() not in raw for value in (h.token, EMAIL, SUB, PASSWORD, SEED))
    payload = h.service._open(ticket)
    assert "session" not in payload
    second = h.service._seal(h.service._owner(payload), h.token, h.record())
    assert ticket != second and h.service._open(second) == payload
    state = DynamoEnrollmentState(h.db, "independent-audit", MASTER, POOL, "differentadminclient")
    # Construct a differently bound verifier with no network used.
    other_sessions = type("OtherSessions", (), {"pool_id": POOL, "client_id": "differentadminclient",
                                                "verified_claims": lambda *_: None})()
    other = CognitoNativeEnrollment(h.provider, other_sessions, state, MASTER,
                                    enrollment_subject=SUB, wallclock=lambda: h.now[0])
    assert other.finish(ticket, "123456") is False
    other_key = CognitoNativeEnrollment(h.provider, h.sessions, h.service._state, b"z" * 32,
                                        enrollment_subject=SUB, wallclock=lambda: h.now[0])
    assert other_key.finish(ticket, "123456") is False
    for altered in (ticket + "=", "e2." + ticket[3:], ticket[:20] + ("A" if ticket[20] != "A" else "B") + ticket[21:]):
        assert h.service.finish(altered, "123456") is False
    assert "verify" not in h.provider.calls


@pytest.mark.parametrize("offset", [181, -31])
def test_expired_and_future_tickets_deny_before_verification(h, offset):
    ticket = h.begin()["enrollment_ticket"]
    h.now[0] += offset
    assert h.service.finish(ticket, "123456") is False
    assert h.service.cancel(ticket) is False
    assert "verify" not in h.provider.calls


@pytest.mark.parametrize("change", ["disabled", "revoked", "nonowner", "verified_email", "email", "factor"])
def test_live_eligibility_is_rechecked_at_finish(h, change):
    ticket = h.begin()["enrollment_ticket"]
    if change == "disabled": h.provider.enabled = False
    elif change == "revoked": h.provider.revoked = True
    elif change == "nonowner": h.provider.groups = []
    elif change == "factor": h.provider.factors = ["SOFTWARE_TOKEN_MFA"]
    else:
        h.provider.current = h.provider.identity()
        h.provider.current["UserAttributes"][2 if change == "verified_email" else 1]["Value"] = "false" if change == "verified_email" else "changed@example.invalid"
    assert h.service.finish(ticket, "123456") is False
    assert "verify" not in h.provider.calls
    assert h.record()["state"] == "PENDING"


def test_wrong_code_retries_same_nonce_and_expiry_with_durable_five_attempt_limit(h):
    ticket = h.begin()["enrollment_ticket"]
    before = h.record()
    for attempt in range(1, 6):
        with pytest.raises(EnrollmentCodeMismatch): h.service.finish(ticket, "000000")
        after = h.record()
        assert after["state"] == "PENDING" and after["phase"] == "READY"
        assert after["nonce"] == before["nonce"] and after["expires"] == before["expires"]
        assert after["attempts"] == attempt
    assert h.service.finish(ticket, "123456") is False
    assert h.provider.calls.count("verify") == 5
    assert h.service.cancel(ticket) is True


def test_wrong_code_then_correct_code_completes_without_new_association(h):
    ticket = h.begin()["enrollment_ticket"]
    with pytest.raises(EnrollmentCodeMismatch): h.service.finish(ticket, "000000")
    assert h.service.finish(ticket, "123456") is True
    assert h.provider.calls.count("associate") == 1


def test_cancelled_or_expired_pending_nonce_cannot_complete_a_replacement_flow(h):
    old = h.begin()["enrollment_ticket"]
    assert h.service.cancel(old) is True
    fresh = h.begin()["enrollment_ticket"]
    assert fresh != old and h.service.finish(old, "123456") is False
    assert h.service.finish(fresh, "123456") is True
    assert h.provider.calls.count("verify") == 1


def test_only_ready_pending_can_expire_into_a_new_claim(h):
    old = h.begin()["enrollment_ticket"]
    previous = h.record()
    h.now[0] += 181
    h.issue()
    fresh = h.begin()["enrollment_ticket"]
    assert h.record()["nonce"] != previous["nonce"]
    assert h.service.finish(old, "123456") is False
    assert h.service.finish(fresh, "123456") is True


def test_two_instances_cannot_verify_the_same_ticket_concurrently(h):
    ticket = h.begin()["enrollment_ticket"]
    other = h.another()
    barrier = Barrier(2)
    for service in (h.service, other):
        previous = service._state.verifying
        def wait(record, previous=previous):
            barrier.wait(timeout=5)
            return previous(record)
        service._state.verifying = wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.finish, ticket, "123456") for service in (h.service, other)]
        assert sorted(future.result(timeout=6) for future in futures) == [False, True]
    assert h.provider.calls.count("verify") == h.provider.calls.count("prefer") == 1


@pytest.mark.parametrize("early_auth", [0, 20])
def test_slow_verifying_cas_cannot_extend_ticket_or_signed_login_deadline(h, early_auth):
    h.issue(auth_time=h.now[0] - early_auth)
    ticket = h.begin()["enrollment_ticket"]
    original = h.service._state.verifying
    def slow(record):
        result = original(record)
        h.now[0] += 181 - early_auth
        return result
    h.service._state.verifying = slow
    with pytest.raises(EnrollmentUnavailable): h.service.finish(ticket, "123456")
    assert "verify" not in h.provider.calls and h.record()["state"] == "VERIFYING"


def test_two_instances_cannot_associate_the_same_owner_concurrently(h):
    other = h.another()
    barrier = Barrier(2)
    for service in (h.service, other):
        previous = service._state.read
        def wait(subject, previous=previous):
            value = previous(subject)
            barrier.wait(timeout=5)
            return value
        service._state.read = wait
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.begin, EMAIL, PASSWORD) for service in (h.service, other)]
        results = [future.result(timeout=6) for future in futures]
    assert sum(value is not None for value in results) == 1
    assert h.provider.calls.count("associate") == 1


@pytest.mark.parametrize("phase,state", [("ASSOCIATING", "PENDING"), ("READY", "VERIFYING"), ("READY", "COMPLETE"), ("READY", "BLOCKED")])
def test_ambiguous_or_terminal_states_never_auto_expire(h, phase, state):
    record = h.service._state.reserve(SUB, "a" * 32, h.now[0])
    h.service._state._put({**record, "phase": phase, "state": state}, record)
    h.now[0] += 10000
    h.issue()
    assert h.begin() is None
    assert "associate" not in h.provider.calls


@pytest.mark.parametrize("where", ["reserve", "ready", "verifying", "complete"])
def test_storage_response_loss_never_continues_an_unconfirmed_transition(h, where):
    def lose(record):
        selected = {"reserve": record["revision"] == 1,
                    "ready": record["revision"] == 2,
                    "verifying": record["state"] == "VERIFYING",
                    "complete": record["state"] == "COMPLETE"}
        return "after" if selected[where] else None
    if where in ("reserve", "ready"):
        h.db.fail_write = lose
        with pytest.raises(EnrollmentUnavailable): h.begin()
        assert h.provider.calls.count("associate") == (1 if where == "ready" else 0)
        assert "verify" not in h.provider.calls
    else:
        ticket = h.begin()["enrollment_ticket"]
        h.db.fail_write = lose
        with pytest.raises(EnrollmentUnavailable): h.service.finish(ticket, "123456")
        assert h.provider.calls.count("verify") == (1 if where == "complete" else 0)
        h.db.fail_write = None
        assert h.service.finish(ticket, "123456") is False
        assert h.record()["state"] in ("VERIFYING", "COMPLETE")


@pytest.mark.parametrize("method", ["associate", "verify", "prefer"])
def test_unknown_provider_outcomes_remain_blocked_and_never_retry(h, method):
    if method == "associate":
        h.provider.fail[method] = "InternalErrorException"
        with pytest.raises(EnrollmentUnavailable): h.begin()
        assert h.record()["phase"] == "ASSOCIATING"
    else:
        ticket = h.begin()["enrollment_ticket"]
        h.provider.fail[method] = "InternalErrorException"
        with pytest.raises(EnrollmentUnavailable): h.service.finish(ticket, "123456")
        assert h.record()["state"] == "VERIFYING"
        assert h.service.cancel(ticket) is False
        assert h.service.finish(ticket, "123456") is False
    h.provider.fail.clear()
    h.provider.factors, h.provider.preferred = [], ""
    h.now[0] += 10000; h.issue()
    assert h.begin() is None
    assert h.provider.calls.count(method) == 1


def test_post_verification_owner_revocation_prevents_preference_write(h):
    ticket = h.begin()["enrollment_ticket"]
    h.provider.hooks["verify"] = lambda: setattr(h.provider, "groups", [])
    with pytest.raises(EnrollmentUnavailable): h.service.finish(ticket, "123456")
    assert "prefer" not in h.provider.calls and h.record()["state"] == "VERIFYING"


@pytest.mark.parametrize("method", ["verify", "prefer"])
def test_provider_response_lost_after_actual_mutation_still_blocks_replay(h, method):
    ticket = h.begin()["enrollment_ticket"]
    def apply_then_lose():
        h.provider.factors = ["SOFTWARE_TOKEN_MFA"]
        if method == "prefer": h.provider.preferred = "SOFTWARE_TOKEN_MFA"
    h.provider.hooks[method] = apply_then_lose
    h.provider.fail[method] = "InternalErrorException"
    with pytest.raises(EnrollmentUnavailable): h.service.finish(ticket, "123456")
    assert h.record()["state"] == "VERIFYING"
    assert h.service.finish(ticket, "123456") is False
    assert h.provider.calls.count(method) == 1


def test_completion_requires_same_control_nonce_after_preference_write(h):
    ticket = h.begin()["enrollment_ticket"]
    def administrative_block():
        current = h.record()
        h.service._state.block(current)
    h.provider.hooks["prefer"] = administrative_block
    with pytest.raises(EnrollmentUnavailable): h.service.finish(ticket, "123456")
    assert h.record()["state"] == "BLOCKED"


@pytest.mark.parametrize("problem", ["verify_error_status", "preference_not_saved", "completion_read_lost"])
def test_indeterminate_results_are_never_reported_as_completed(h, problem):
    ticket = h.begin()["enrollment_ticket"]
    if problem == "verify_error_status": h.provider.verify_status = "ERROR"
    elif problem == "preference_not_saved": h.provider.preference_persisted = False
    else:
        previous = h.service._state.complete
        def lose_read(record):
            result = previous(record)
            h.db.fail_read = True
            return result
        h.service._state.complete = lose_read
    with pytest.raises(EnrollmentUnavailable): h.service.finish(ticket, "123456")


def test_unavailable_control_storage_fails_before_any_provider_mutation(h):
    h.db.fail_read = True
    with pytest.raises(EnrollmentUnavailable): h.begin()
    assert "associate" not in h.provider.calls


@pytest.mark.parametrize("corruption", ["mac", "unexpected_item", "receipt", "malformed", "invalid_phase", "duplicate_json"])
def test_corrupt_control_rows_never_become_absence_or_overwrite_audit_receipts(h, corruption):
    record = h.service._state.reserve(SUB, "a" * 32, h.now[0])
    key = h.service._state._id(SUB)
    if corruption == "mac":
        record["nonce"] = "b" * 32
        h.db.items[key]["enrollment_control"]["S"] = h.service._state._json(record)
    elif corruption == "unexpected_item": h.db.items[key]["extra"] = {"S": "unexpected"}
    elif corruption == "receipt": h.db.items[key] = {"event_id": {"S": key}, "operation": {"S": "admin.read"}}
    elif corruption == "malformed": h.db.items[key]["enrollment_control"] = {"S": "{}"}
    elif corruption == "invalid_phase":
        value = h.service._state._signed({**record, "state": "COMPLETE", "phase": "ASSOCIATING"})
        h.db.items[key]["enrollment_control"]["S"] = h.service._state._json(value)
    else:
        h.db.items[key]["enrollment_control"]["S"] = h.service._state._json(record)[:-1] + ',"version":1}'
    before = deepcopy(h.db.items)
    with pytest.raises(EnrollmentStateUnavailable): h.service._state.reserve(SUB, "b" * 32, h.now[0] + 10000)
    assert h.db.items == before
    assert key.startswith("enrollment-control:")
    with pytest.raises(ValueError): AdminAuditReader(h.db, "independent-audit").read(key)


def test_cas_compares_full_document_and_rejects_stale_nonce(h):
    state = h.service._state
    old = state.reserve(SUB, "a" * 32, h.now[0])
    ready = state.ready(old)
    cancelled = state.cancel(ready)
    assert cancelled is not None
    current = state.reserve(SUB, "b" * 32, h.now[0])
    assert state.verifying(ready) is None
    assert state.read(SUB) == current


def test_rate_and_concurrency_bounds_deny_before_provider_calls(h):
    h.provider.fail["password"] = "NotAuthorizedException"
    for _ in range(12): assert h.begin() is None
    with pytest.raises(EnrollmentUnavailable): h.begin()
    assert len(h.provider.calls) == 12
    h.now[0] += 61
    assert h.begin() is None
    assert h.service._slots.acquire(False) and h.service._slots.acquire(False)
    with pytest.raises(EnrollmentUnavailable): h.begin()
    h.service._slots.release(); h.service._slots.release()
    assert len(h.provider.calls) == 13


def test_api_complete_means_setup_only_and_contains_no_token(h):
    client = h.client()
    assert client.get("/v1/admin/auth/config").json()["native_enrollment_enabled"] is True
    start = client.post("/v1/admin/auth/enrollment/start", json={"email": EMAIL, "password": PASSWORD}, headers=ORIGIN)
    assert start.status_code == 200
    assert all(value not in start.text for value in (h.token, PASSWORD, "RefreshToken"))
    ticket = start.json()["enrollment_ticket"]
    finish = client.post("/v1/admin/auth/enrollment/finish", json={"ticket": ticket, "code": "123456"}, headers=ORIGIN)
    assert finish.status_code == 200 and finish.json() == {"status": "completed", "sign_in_required": True}
    assert "access_token" not in finish.text
    for response in (start, finish):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["pragma"] == "no-cache"
        assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize("origin", [None, "https://evil.invalid", "http://localhost:8777", "https://guardoath.com, https://evil.invalid"])
def test_api_rejects_untrusted_origin_before_parsing_or_provider(h, origin):
    headers = {"Origin": origin, "Content-Type": "application/json"} if origin is not None else {"Content-Type": "application/json"}
    result = h.client().post("/v1/admin/auth/enrollment/start", content="malformed-password", headers=headers)
    assert result.status_code == 403 and "malformed-password" not in result.text
    assert not h.provider.calls


@pytest.mark.parametrize("body", [
    {"email": EMAIL, "password": PASSWORD, "access_token": "browser-supplied-token"},
    {"email": EMAIL, "password": [PASSWORD]},
    {"email": EMAIL}, [PASSWORD],
])
def test_validation_does_not_reflect_credentials_or_accept_browser_token(h, body):
    result = h.client().post("/v1/admin/auth/enrollment/start", json=body, headers=ORIGIN)
    assert result.status_code == 400
    assert all(value not in result.text for value in (EMAIL, PASSWORD, "browser-supplied-token"))
    assert not h.provider.calls


def test_duplicate_and_oversized_requests_are_bounded_and_generic(h):
    c = h.client()
    headers = {**ORIGIN, "Content-Type": "application/json"}
    duplicate = '{"email":"owner@example.invalid","password":"secret-one","password":"secret-two"}'
    result = c.post("/v1/admin/auth/enrollment/start", content=duplicate, headers=headers)
    assert result.status_code == 400 and "secret-" not in result.text
    result = c.post("/v1/admin/auth/enrollment/start", content=b"x" * 24001, headers=headers)
    assert result.status_code == 413 and not h.provider.calls


def test_api_wrong_code_can_retry_but_provider_failure_is_generic_and_terminal(h):
    c = h.client()
    ticket = h.begin()["enrollment_ticket"]
    result = c.post("/v1/admin/auth/enrollment/finish", json={"ticket": ticket, "code": "000000"}, headers=ORIGIN)
    assert result.status_code == 400 and result.json()["code"] == "code_mismatch"
    h.provider.fail["verify"] = "InternalErrorException"
    result = c.post("/v1/admin/auth/enrollment/finish", json={"ticket": ticket, "code": "123456"}, headers=ORIGIN)
    assert result.status_code == 503
    assert all(value not in result.text for value in ("private-provider-detail", ticket, h.token, SEED, "123456"))
    assert h.record()["state"] == "VERIFYING"


def test_cancel_api_only_clears_pending_flow_and_does_not_return_session(h):
    ticket = h.begin()["enrollment_ticket"]
    result = h.client().post("/v1/admin/auth/enrollment/cancel", json={"ticket": ticket}, headers=ORIGIN)
    assert result.status_code == 200 and result.json() == {"status": "cancelled"}
    assert h.record()["state"] == "CANCELLED"
    assert "verify" not in h.provider.calls


def runtime_environment(monkeypatch, *, native="1", audit="independent-audit", subject=SUB):
    for name, value in {"GUARDOATH_ADMIN_ENABLED": "1", "GUARDOATH_COGNITO_POOL_ID": POOL,
                        "GUARDOATH_ADMIN_COGNITO_DOMAIN": DOMAIN, "GUARDOATH_ADMIN_CLIENT_ID": CLIENT,
                        "GUARDOATH_ADMIN_CURSOR_HMAC_KEY": MASTER.hex(),
                        "GUARDOATH_ADMIN_NATIVE_MFA_ENABLED": native,
                        "GUARDOATH_ADMIN_ENROLLMENT_SUBJECT": subject,
                        "GUARDOATH_ADMIN_AUDIT_TABLE": audit}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GUARDOATH_STRIPE_SECRET_KEY", raising=False)


def runtime_tenants(h):
    class Store:
        table_name = "tenant-table"
        _client = h.db
        def get(self, _): raise AssertionError("Enrollment must not read or grant a customer tenant")
    return type("Tenants", (), {"store": Store()})()


@pytest.mark.parametrize("native,audit,enabled", [("0", "", False), ("1", "", False),
                                                 ("0", "independent-audit", False), ("1", "independent-audit", True)])
def test_enrollment_only_wires_with_native_opt_in_and_independent_audit(h, monkeypatch, native, audit, enabled):
    from guardoath.admin_runtime import configure_admin_from_environment
    runtime_environment(monkeypatch, native=native, audit=audit)
    app = FastAPI()
    configure_admin_from_environment(app, tenants=runtime_tenants(h), admin_sessions=h.sessions,
                                     cognito_client=h.provider)
    c = TestClient(app)
    assert c.get("/v1/admin/auth/config").json()["native_enrollment_enabled"] is enabled
    result = c.post("/v1/admin/auth/enrollment/start", json={}, headers=ORIGIN)
    assert result.status_code == (400 if enabled else 404)
    assert not h.db.calls and not h.provider.calls


def test_runtime_refuses_customer_table_for_enrollment_before_any_routes(h, monkeypatch):
    from guardoath.admin_integration import AdminConfigurationError
    from guardoath.admin_runtime import configure_admin_from_environment
    runtime_environment(monkeypatch, audit="tenant-table")
    app = FastAPI()
    with pytest.raises(AdminConfigurationError, match="independent"):
        configure_admin_from_environment(app, tenants=runtime_tenants(h), admin_sessions=h.sessions,
                                         cognito_client=h.provider)
    assert TestClient(app).get("/admin/login").status_code == 404
    assert not h.db.calls and not h.provider.calls


def test_production_enrollment_sdk_has_no_automatic_retries(h, monkeypatch):
    import sys
    from types import SimpleNamespace
    from guardoath.admin_runtime import configure_admin_from_environment
    runtime_environment(monkeypatch)
    created = []
    def client(name, **kwargs):
        assert name == "cognito-idp" and kwargs["region_name"] == "us-east-1"
        created.append(kwargs["config"])
        return h.provider
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "botocore.config", SimpleNamespace(Config=lambda **kwargs: kwargs))
    configure_admin_from_environment(FastAPI(), tenants=runtime_tenants(h), admin_sessions=h.sessions)
    assert created == [
        {"connect_timeout": 1, "read_timeout": 2, "retries": {"total_max_attempts": 2}},
        {"connect_timeout": 1, "read_timeout": 2, "retries": {"total_max_attempts": 1}}]
    assert not h.provider.calls and not h.db.calls


def test_mount_refuses_unbound_or_spoofed_enrollment_before_routes(h):
    from guardoath.admin_integration import AdminConfigurationError, mount_admin_console
    from types import SimpleNamespace
    config = BrowserAuthConfig(DOMAIN, CLIENT, "https://guardoath.com/admin/login")
    native = CognitoNativeMfa(h.provider, CLIENT, MASTER)
    args = dict(sessions=h.sessions, tenants=runtime_tenants(h), cognito=h.provider,
                cursor_signing_key=MASTER, browser_config=config,
                browser_exchange=BoundExchange(config), native_auth=native)
    for invalid in (SimpleNamespace(pool_id=POOL, client_id=CLIENT), h.another()):
        if isinstance(invalid, CognitoNativeEnrollment): invalid.pool_id = "us-east-1_Wrong"
        app = FastAPI()
        with pytest.raises(AdminConfigurationError, match="enrollment"):
            mount_admin_console(app, **args, native_enrollment=invalid)
        assert TestClient(app).get("/admin/login").status_code == 404


def test_eligible_owner_without_matching_operator_subject_grant_cannot_begin(h):
    h.service.enrollment_subject = "00000000-0000-0000-0000-000000000000"
    assert h.begin() is None
    assert h.provider.calls == ["password"]
    assert not h.db.items


def test_subject_grant_is_rechecked_for_existing_ticket(h):
    ticket = h.begin()["enrollment_ticket"]
    other = h.another()
    other.enrollment_subject = "00000000-0000-0000-0000-000000000000"
    assert other.finish(ticket, "123456") is False
    assert "verify" not in h.provider.calls


@pytest.mark.parametrize("subject", [None, ""])
def test_unset_operator_grant_omits_enrollment_but_keeps_native_login(h, monkeypatch, subject):
    from guardoath.admin_runtime import configure_admin_from_environment
    runtime_environment(monkeypatch, subject="")
    if subject is None: monkeypatch.delenv("GUARDOATH_ADMIN_ENROLLMENT_SUBJECT")
    app = FastAPI()
    configure_admin_from_environment(app, tenants=runtime_tenants(h), admin_sessions=h.sessions,
                                     cognito_client=h.provider)
    c = TestClient(app)
    config = c.get("/v1/admin/auth/config").json()
    assert config["native_mfa_enabled"] is True and config["native_enrollment_enabled"] is False
    assert SUB not in json.dumps(config)
    assert c.post("/v1/admin/auth/enrollment/start", json={}, headers=ORIGIN).status_code == 404
    # The existing password-only rejection still comes from native login, not a missing route.
    assert c.post("/v1/admin/auth/native/start", json={"email": EMAIL, "password": PASSWORD}, headers=ORIGIN).status_code == 401


@pytest.mark.parametrize("subject", [EMAIL, "*", " ", SUB + "," + SUB, "not-a-subject"])
def test_malformed_operator_grant_fails_startup_without_revealing_identity(h, monkeypatch, subject):
    from guardoath.admin_integration import AdminConfigurationError
    from guardoath.admin_runtime import configure_admin_from_environment
    runtime_environment(monkeypatch, subject=subject)
    app = FastAPI()
    with pytest.raises(AdminConfigurationError) as failure:
        configure_admin_from_environment(app, tenants=runtime_tenants(h), admin_sessions=h.sessions,
                                         cognito_client=h.provider)
    if subject.strip():
        assert subject not in str(failure.value)
    assert TestClient(app).get("/admin/login").status_code == 404
