from dataclasses import dataclass
import time
import pytest
from guardoath.admin_cognito import verify_admin, make_verifier
from guardoath.admin_console import create_admin_router
from fastapi import FastAPI
from fastapi.testclient import TestClient

SUB = "12345678-1234-1234-1234-123456789012"

@dataclass
class Sessions:
    pool_id: str = "us-east-1_example"
    claims: dict | None = None
    def verified_claims(self, token):
        return self.claims if token == "real-signed-token" else None

class Provider:
    def __init__(self):
        self.enabled = True
        self.status = "CONFIRMED"
        self.sub = SUB
        self.groups = [{"GroupName": "GuardOathOwners"}]
        self.next_token = None
        self.calls = []
        self.error = False
        self.token_revoked = False
    def get_user(self, *, AccessToken):
        self.calls.append(("token", AccessToken))
        if self.token_revoked: raise RuntimeError("revoked token")
        return {"Username": "operator"}
    def admin_get_user(self, *, UserPoolId, Username):
        self.calls.append(("user", UserPoolId, Username))
        if self.error: raise RuntimeError("provider secret")
        return {"Enabled": self.enabled, "UserStatus": self.status,
                "UserAttributes": [{"Name": "sub", "Value": self.sub}]}
    def admin_list_groups_for_user(self, *, UserPoolId, Username, Limit):
        self.calls.append(("groups", UserPoolId, Username, Limit))
        return {"Groups": self.groups, **({"NextToken": self.next_token} if self.next_token else {})}

def fixture():
    sessions = Sessions(claims={"sub": SUB, "username": "operator", "amr": ["pwd", "otp", "mfa"], "auth_time": int(time.time())})
    return sessions, Provider()

def test_verified_owner_from_trusted_provider_and_signed_mfa():
    s, c = fixture()
    r = verify_admin("real-signed-token", s, c)
    assert r.subject == SUB and r.mfa_verified and r.roles == frozenset({"owner"})
    assert [call[0] for call in c.calls] == ["token", "user", "groups"]

def test_unsigned_or_unrecognized_session_is_denied_before_provider():
    s, c = fixture()
    assert verify_admin("customer-key", s, c) is None
    assert c.calls == []

def test_unproved_mfa_is_denied_even_if_user_is_admin():
    s, c = fixture()
    for amr in ([], ["pwd"], ["otp"], ["pwd", "otp"], ["totp"],
                ["software_token"], ["pwd", "software_token"], None, "mfa", ["unknown"]):
        s.claims["amr"] = amr
        assert verify_admin("real-signed-token", s, c) is None
    assert c.calls == []

def test_unverified_group_claim_does_not_grant_role():
    s, c = fixture()
    s.claims["cognito:groups"] = ["GuardOathOwners"]
    c.groups = [{"GroupName": "Customers"}]
    assert verify_admin("real-signed-token", s, c) is None

def test_user_disabled_or_wrong_provider_sub_denied():
    s, c = fixture()
    c.enabled = False
    assert verify_admin("real-signed-token", s, c) is None
    c.enabled = True
    c.sub = "00000000-0000-0000-0000-000000000000"
    assert verify_admin("real-signed-token", s, c) is None

def test_provider_group_pagination_fails_closed():
    s, c = fixture()
    c.next_token = "page2"
    assert verify_admin("real-signed-token", s, c) is None

def test_provider_failure_returns_503_not_authorization():
    s, c = fixture()
    c.error = True
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=make_verifier(s,c)))
    response = TestClient(app).get("/admin", headers={"Authorization":"Bearer real-signed-token"})
    assert response.status_code == 503 and "provider secret" not in response.text

def test_authorization_router_allows_owner_and_denies_unproved_mfa():
    s, c = fixture()
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=make_verifier(s,c)))
    client = TestClient(app)
    assert client.get("/admin", headers={"Authorization":"Bearer real-signed-token"}).status_code == 200
    s.claims["amr"] = ["pwd"]
    assert client.get("/admin", headers={"Authorization":"Bearer real-signed-token"}).status_code == 401

def test_revoked_token_cannot_access_admin():
    s, c = fixture()
    c.token_revoked = True
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=make_verifier(s,c)))
    response = TestClient(app).get("/admin", headers={"Authorization":"Bearer real-signed-token"})
    assert response.status_code == 503


def test_expired_future_or_missing_signed_login_time_denied_even_with_mfa(monkeypatch):
    import guardoath.admin_cognito as module
    monkeypatch.setattr(module, "time", lambda: 100_000)
    s, provider = fixture()
    s.claims["amr"] = ["pwd", "otp", "mfa"]
    for bad in (None, True, "99900", 0, 99_099, 100_031):
        if bad is None:
            s.claims.pop("auth_time", None)
        else:
            s.claims["auth_time"] = bad
        assert verify_admin("real-signed-token", s, provider) is None
    assert provider.calls == []
    for allowed in (99_100, 100_000, 100_030):
        s.claims["auth_time"] = allowed
        assert verify_admin("real-signed-token", s, provider) is not None
    assert len(provider.calls) == 9


def test_refreshed_token_cannot_extend_original_mfa_age(monkeypatch):
    import guardoath.admin_cognito as module
    s, provider = fixture()
    s.claims["auth_time"] = 80_000
    monkeypatch.setattr(module, "time", lambda: 81_000)
    assert verify_admin("real-signed-token", s, provider) is None
    assert provider.calls == []


@pytest.mark.parametrize("response", [
    {"Groups": [{"GroupName": "GuardOathOwners"}], "NextToken": ""},
    {"Groups": [{"GroupName": "GuardOathOwners"}], "NextToken": "more"},
    {"Groups": [{"GroupName": "GuardOathOwners"}], "NextToken": False},
    {"Groups": [{"GroupName": "GuardOathOwners"}], "NextToken": []},
    {"Groups": [{"GroupName": "GuardOathOwners"}, {"GroupName": "GuardOathOwners"}]},
    {"Groups": [{"GroupName": "GuardOathOwners"}, {"GroupName": "Viewer"},
                {"GroupName": "Viewer"}]},
    {"Groups": [{"GroupName": "GuardOathOwners"}, None]},
    {"Groups": [{"GroupName": "GuardOathOwners"}, {"Name": "MissingGroupName"}]},
    {"Groups": [{"GroupName": "GuardOathOwners"}, {"GroupName": 5}]},
    {"Groups": [{"GroupName": "GuardOathOwners"}, {"GroupName": "x" * 129}]},
    {"Groups": [{"GroupName": "GuardOathOwners"}] * 61},
    {"Groups": "GuardOathOwners"},
    {"Groups": []},
    {"Groups": None},
    {},
])
def test_owner_membership_must_be_complete_unique_and_well_formed(response):
    sessions, provider = fixture()
    provider.admin_list_groups_for_user = lambda **kwargs: response
    assert verify_admin("real-signed-token", sessions, provider) is None
    app = FastAPI()
    app.include_router(create_admin_router(verify_cognito_admin=make_verifier(sessions, provider)))
    client = TestClient(app)
    headers = {"Authorization": "Bearer real-signed-token"}
    assert client.get("/admin", headers=headers).status_code == 401
    assert client.get("/v1/admin/tenants", headers=headers).status_code == 401


def test_owner_with_distinct_additional_groups_remains_authorized():
    sessions, provider = fixture()
    provider.groups = [{"GroupName": "BillingReaders"},
                       {"GroupName": "GuardOathOwners"},
                       {"GroupName": "Customers"}]
    principal = verify_admin("real-signed-token", sessions, provider)
    assert principal is not None
    assert principal.roles == frozenset({"owner"}) and principal.mfa_verified
