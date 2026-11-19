from dataclasses import dataclass
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
    sessions = Sessions(claims={"sub": SUB, "username": "operator", "amr": ["pwd", "mfa"]})
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
    for amr in ([], ["pwd"], None, "mfa", ["unknown"]):
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
