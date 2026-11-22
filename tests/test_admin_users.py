"""Provider failures, cursor forgery, leakage and bounds fail closed."""
import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
import pytest
from guardoath.admin_users import CognitoUserDirectory, InvalidUserCursor, CognitoDirectoryUnavailable

OWNER = "12345678-1234-1234-1234-123456789012"
OTHER_OWNER = "87654321-4321-4321-4321-210987654321"

class Fake:
    def __init__(self): self.calls=[]
    def list_users(self, **kw):
        self.calls.append(kw)
        if "PaginationToken" in kw: return {"Users":[]}
        return {"Users":[{"Username":"test-subject", "Enabled":True, "UserStatus":"CONFIRMED",
                         "UserCreateDate":datetime(2026,11,1,tzinfo=timezone.utc),
                         "Attributes":[{"Name":"email","Value":"someone@example.com"},
                                       {"Name":"email_verified","Value":"true"},
                                       {"Name":"custom:tenant_id","Value":"private-tenant"},
                                       {"Name":"custom:token","Value":"private-token"}]}],
                "PaginationToken":"next-provider"}

def directory(fake=None, clock=lambda: 100):
    return CognitoUserDirectory(fake or Fake(), "us-east-1_Example", b"a"*32, clock)

def test_list_is_one_bounded_page_masked_and_paginates():
    f=Fake(); d=directory(f); first=d.page(owner_sub=OWNER, limit=5,query="someone@")
    assert len(f.calls)==1 and f.calls[0]=={"UserPoolId":"us-east-1_Example","Limit":5,"Filter":'email ^= "someone@"'}
    assert first["users"][0] == {"username":first["users"][0]["username"],"email_hint":"s***@example.com",
                                 "email_verified":True,"enabled":True,"status":"CONFIRMED",
                                 "created_utc":"2026-11-01T00:00:00Z","updated_utc":None}
    assert first["users"][0]["username"].startswith("user-") and len(first["users"][0]["username"]) == 21
    assert "test-subject" not in str(first)
    assert first["total"] is None and first["next_cursor"]
    assert "someone@example.com" not in str(first) and "private-tenant" not in str(first)
    after=d.page(owner_sub=OWNER, limit=5,query="someone@",cursor=first["next_cursor"])
    assert after["users"]==[] and after["next_cursor"] is None
    assert f.calls[-1]["PaginationToken"]=="next-provider"


def test_cursor_seals_provider_token_and_owner_filter_without_exposing_plaintext():
    provider = Fake()
    service = directory(provider)
    query = "someone@example.com"
    cursor = service.page(owner_sub=OWNER, limit=5, query=query)["next_cursor"]
    assert cursor.startswith("v4.")
    raw = cursor.split(".", 1)[1]
    ciphertext = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    assert ciphertext and b"next-provider" not in ciphertext
    assert OWNER.encode() not in ciphertext and query.encode() not in ciphertext
    assert "next-provider" not in cursor and OWNER not in cursor and query not in cursor
    # Independent encryptions use unpredictable nonces, never repeat a bearer
    # provider token's browser-visible representation.
    other = service.page(owner_sub=OWNER, limit=5, query=query)["next_cursor"]
    assert cursor != other
    resumed = directory(provider).page(owner_sub=OWNER, limit=5, query=query, cursor=cursor)
    assert resumed["status"] == "ok"
    assert provider.calls[-1]["PaginationToken"] == "next-provider"


def test_cursor_ciphertext_tamper_and_key_rotation_fail_before_provider_read():
    provider = Fake()
    service = directory(provider)
    cursor = service.page(owner_sub=OWNER, limit=5)["next_cursor"]
    raw = cursor.split(".", 1)[1]
    sealed = bytearray(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    for offset in (0, 12, len(sealed) - 1):
        altered = bytearray(sealed)
        altered[offset] ^= 1
        forged = "v4." + base64.urlsafe_b64encode(altered).rstrip(b"=").decode()
        with pytest.raises(InvalidUserCursor):
            service.page(owner_sub=OWNER, limit=5, cursor=forged)
    rotated = CognitoUserDirectory(provider, "us-east-1_Example", b"b"*32, lambda: 100)
    with pytest.raises(InvalidUserCursor):
        rotated.page(owner_sub=OWNER, limit=5, cursor=cursor)
    assert len(provider.calls) == 1


def test_long_provider_continuation_fits_bounded_encrypted_cursor():
    class LongToken(Fake):
        def list_users(self, **kw):
            self.calls.append(kw)
            return {"Users": [], "PaginationToken": "T" * 2048} if "PaginationToken" not in kw else {"Users": []}
    provider = LongToken()
    service = directory(provider)
    cursor = service.page(owner_sub=OWNER, limit=5)["next_cursor"]
    assert cursor.startswith("v4.") and len(cursor) < 4096
    assert "T" * 64 not in cursor
    assert service.page(owner_sub=OWNER, limit=5, cursor=cursor)["next_cursor"] is None
    assert provider.calls[-1]["PaginationToken"] == "T" * 2048


@pytest.mark.parametrize("legacy_version", [2, 3])
def test_legacy_signed_but_cleartext_cursors_fail_closed_before_provider_read(legacy_version):
    provider = Fake()
    service = directory(provider)
    payload = {"v": legacy_version, "pool": "us-east-1_Example", "owner": OWNER, "q": "some", "n": 5,
               "token": "next-provider", "exp": 1000}
    data = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).rstrip(b"=").decode("ascii")
    tag = base64.urlsafe_b64encode(
        hmac.new(b"a" * 32, b"guardoath-user-directory:" + data.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode("ascii")
    with pytest.raises(InvalidUserCursor):
        service.page(owner_sub=OWNER, limit=5, query="some", cursor=data + "." + tag)
    assert provider.calls == []


@pytest.mark.parametrize("attributes", [
    [{"Name": "email", "Value": "one@example.com"},
     {"Name": "email", "Value": "two@example.com"}],
    [{"Name": "email_verified", "Value": "true"},
     {"Name": "email_verified", "Value": "false"}],
    [{"Name": "email", "Value": 123}],
    [{"Name": "email_verified", "Value": None}],
])
def test_ambiguous_or_malformed_user_directory_identity_fails_closed(attributes):
    class Ambiguous(Fake):
        def list_users(self, **kw):
            self.calls.append(kw)
            return {"Users": [{"Username": "anonymous-subject",
                               "Attributes": attributes}]}
    provider = Ambiguous()
    with pytest.raises(CognitoDirectoryUnavailable, match="Invalid Cognito directory response"):
        directory(provider).page(owner_sub=OWNER)
    assert len(provider.calls) == 1

@pytest.mark.parametrize("query",['broken"',"bad\\","é"," a","a"*81])
def test_reject_unsafe_filters_before_provider(query):
    f=Fake()
    with pytest.raises(ValueError): directory(f).page(owner_sub=OWNER, query=query)
    assert f.calls==[]

@pytest.mark.parametrize("size",[0,51,True,1.5])
def test_reject_invalid_page_sizes(size):
    f=Fake()
    with pytest.raises(ValueError): directory(f).page(owner_sub=OWNER, limit=size)
    assert f.calls==[]

def test_invalid_expired_or_changed_cursor_never_reaches_provider():
    f=Fake(); d=directory(f); c=d.page(owner_sub=OWNER, limit=2,query="a")["next_cursor"]
    for cur,query,limit in [(c+"x","a",2),(c,"b",2),(c,"a",3),("é","a",2)]:
        with pytest.raises(InvalidUserCursor): d.page(owner_sub=OWNER, cursor=cur,query=query,limit=limit)
    with pytest.raises(InvalidUserCursor): directory(f,clock=lambda:2000).page(owner_sub=OWNER, cursor=c,query="a",limit=2)
    assert len(f.calls)==1

def test_provider_oversize_or_unexpected_attributes_fail_closed():
    class Oversize(Fake):
        def list_users(self,**kw): return {"Users":[{"Username":"x"}]*51}
    with pytest.raises(CognitoDirectoryUnavailable): directory(Oversize()).page(owner_sub=OWNER, )

def test_missing_provider_or_weak_key_is_rejected():
    with pytest.raises(ValueError): CognitoUserDirectory(object(),"us-east-1_Example",b"a"*32)
    with pytest.raises(ValueError): CognitoUserDirectory(Fake(),"us-east-1_Example",b"short")


def test_cognito_email_username_is_masked():
    class EmailUsername(Fake):
        def list_users(self, **kw):
            return {"Users": [{"Username": "person@example.com",
                               "Attributes": [{"Name": "email", "Value": "person@example.com"}]}]}
    result = directory(EmailUsername()).page(owner_sub=OWNER, )
    assert result["users"][0]["username"] == "p***@example.com"
    assert "person@example.com" not in str(result)


def test_opaque_cognito_username_is_stable_keyed_and_never_leaks_raw_subject():
    class InternalUsername(Fake):
        def list_users(self, **kw):
            return {"Users": [{"Username": "internal-account-admin-492843",
                               "Enabled": True, "Attributes": []}]}
    provider = InternalUsername()
    a = CognitoUserDirectory(provider, "us-east-1_Example", b"a" * 32, lambda: 100).page(owner_sub=OWNER, )
    b = CognitoUserDirectory(provider, "us-east-1_Example", b"a" * 32, lambda: 100).page(owner_sub=OWNER, )
    c = CognitoUserDirectory(provider, "us-east-1_Example", b"b" * 32, lambda: 100).page(owner_sub=OWNER, )
    label = a["users"][0]["username"]
    assert label.startswith("user-") and len(label) == 21
    assert label == b["users"][0]["username"] and label != c["users"][0]["username"]
    assert "internal-account-admin-492843" not in str(a)
    assert "internal-account-admin-492843" not in str(b)
    assert "internal-account-admin-492843" not in str(c)


def test_owner_cursor_cannot_be_replayed_by_another_verified_admin():
    provider = Fake()
    service = directory(provider)
    cursor = service.page(owner_sub=OWNER, limit=5, query="some")["next_cursor"]
    assert cursor
    with pytest.raises(InvalidUserCursor):
        service.page(owner_sub=OTHER_OWNER, limit=5, query="some", cursor=cursor)
    assert len(provider.calls) == 1  # no second Cognito provider read


@pytest.mark.parametrize("invalid", ["", "user@example.com", "x"*81, True, None])
def test_invalid_owner_subject_fails_before_cognito_read(invalid):
    provider = Fake()
    with pytest.raises(ValueError):
        directory(provider).page(owner_sub=invalid)
    assert provider.calls == []

def test_exact_account_inspection_uses_one_bounded_email_match_and_no_private_identity():
    # ListUsers prefix pagination fixture has a continuation token; an exact
    # identity lookup must have no continuation in order to prove uniqueness.
    class ExactProvider(Fake):
        def list_users(self, **kw):
            result = super().list_users(**kw)
            result.pop("PaginationToken", None)
            return result
    provider = ExactProvider()
    result = directory(provider).lookup(owner_sub=OWNER, email="someone@example.com")
    assert result["status"] == "ok"
    assert result["user"]["email_hint"] == "s***@example.com"
    assert result["user"]["status"] == "CONFIRMED"
    assert result["user"]["enabled"] is True
    assert "someone@example.com" not in str(result)
    assert "test-subject" not in str(result)
    assert "private-token" not in str(result)
    assert provider.calls == [{"UserPoolId":"us-east-1_Example",
                               "Filter":'email = "someone@example.com"',"Limit":2}]


def test_exact_user_missing_is_not_fabricated_zero_count():
    class Missing(Fake):
        def list_users(self, **kw):
            self.calls.append(kw)
            return {"Users":[]}
    provider = Missing()
    result = directory(provider).lookup(owner_sub=OWNER, email="nobody@example.com")
    assert result["status"] == "not_found" and result["user"] is None
    assert "total" not in result and len(provider.calls) == 1


@pytest.mark.parametrize("email", [
    'bad"@example.com', "a\\b@example.com", "no-at", "a@localhost",
    ".x@example.com", "x..y@example.com", "x@example.com.", "a@example.com;admin",
    "x" * 250 + "@example.com", True, None, "",
])
def test_exact_email_injection_or_invalid_input_never_calls_cognito(email):
    from guardoath.admin_users import InvalidUserLookup
    provider = Fake()
    with pytest.raises(InvalidUserLookup):
        directory(provider).lookup(owner_sub=OWNER, email=email)
    assert provider.calls == []


def test_exact_user_owner_cannot_be_claimed_from_email():
    from guardoath.admin_users import InvalidUserLookup
    provider = Fake()
    with pytest.raises(InvalidUserLookup):
        directory(provider).lookup(owner_sub="customer@example.com", email="someone@example.com")
    assert provider.calls == []


@pytest.mark.parametrize("users", [
    [{"Username":"one","Attributes":[{"Name":"email","Value":"someone@example.com"}]},
     {"Username":"two","Attributes":[{"Name":"email","Value":"someone@example.com"}]}],
    [{"Username":"one","Attributes":[{"Name":"email","Value":"different@example.com"}]}],
    [{"Username":"one","Attributes":[]}],
    [{"Username":"one","Attributes":[{"Name":"email","Value":"someone@example.com"},
                                      {"Name":"email","Value":"someone@example.com"}]}],
])
def test_exact_user_ambiguous_or_inconsistent_provider_data_fails_closed(users):
    from guardoath.admin_users import CognitoDirectoryUnavailable
    class Inconsistent(Fake):
        def list_users(self, **kw):
            self.calls.append(kw)
            return {"Users":users}
    provider = Inconsistent()
    with pytest.raises(CognitoDirectoryUnavailable):
        directory(provider).lookup(owner_sub=OWNER, email="someone@example.com")
    assert len(provider.calls) == 1


def test_exact_user_provider_error_never_claims_success():
    from guardoath.admin_users import CognitoDirectoryUnavailable
    class Offline(Fake):
        def list_users(self, **kw): raise RuntimeError("private internal details")
    with pytest.raises(CognitoDirectoryUnavailable):
        directory(Offline()).lookup(owner_sub=OWNER,email="someone@example.com")

def test_exact_lookup_refuses_paged_single_match_without_global_uniqueness_proof():
    from guardoath.admin_users import CognitoDirectoryUnavailable
    class Paged(Fake):
        def list_users(self, **kwargs):
            self.calls.append(kwargs)
            return {"Users":[{"Username":"subject",
                "Attributes":[{"Name":"email","Value":"someone@example.com"}]}],
                "PaginationToken":"more-identities"}
    provider = Paged()
    with pytest.raises(CognitoDirectoryUnavailable):
        directory(provider).lookup(owner_sub=OWNER,email="someone@example.com")
    assert len(provider.calls) == 1


def test_missing_email_verification_is_unknown_not_false():
    class NoVerification(Fake):
        def list_users(self, **kwargs):
            return {"Users":[{"Username":"subject","Enabled":True,
                "Attributes":[{"Name":"email","Value":"someone@example.com"}]}]}
    result = directory(NoVerification()).lookup(owner_sub=OWNER,email="someone@example.com")
    assert result["user"]["email_verified"] is None

def test_exact_lookup_rate_limit_rejects_before_provider_and_resets_next_minute():
    from guardoath.admin_users import CognitoLookupRateLimited
    clock = [100.0]
    class SmallProvider(Fake):
        def list_users(self, **kwargs):
            self.calls.append(kwargs)
            return {"Users":[]}
    provider = SmallProvider()
    service = directory(provider,clock=lambda: clock[0])
    for _ in range(20):
        assert service.lookup(owner_sub=OWNER,email="someone@example.com")["status"] == "not_found"
    with pytest.raises(CognitoLookupRateLimited):
        service.lookup(owner_sub=OWNER,email="someone@example.com")
    assert len(provider.calls) == 20
    clock[0] = 160.01
    assert service.lookup(owner_sub=OWNER,email="someone@example.com")["status"] == "not_found"
    assert len(provider.calls) == 21


def test_exact_lookup_rate_window_rejects_untrusted_clock_without_provider_call():
    from guardoath.admin_users import CognitoDirectoryUnavailable
    provider = Fake()
    service = directory(provider,clock=lambda: float("nan"))
    with pytest.raises(CognitoDirectoryUnavailable):
        service.lookup(owner_sub=OWNER,email="someone@example.com")
    assert provider.calls == []


def test_directory_pagination_shares_twenty_call_budget_and_blocks_before_cognito():
    from guardoath.admin_users import CognitoLookupRateLimited
    clock = [100.0]
    provider = Fake()
    service = directory(provider, clock=lambda: clock[0])
    for _ in range(20):
        assert service.page(owner_sub=OWNER, limit=2)["status"] == "ok"
    with pytest.raises(CognitoLookupRateLimited):
        service.page(owner_sub=OWNER, limit=2)
    assert len(provider.calls) == 20
    # Verified owners have independent windows; the same owner can retry after expiry.
    assert service.page(owner_sub=OTHER_OWNER, limit=2)["status"] == "ok"
    assert len(provider.calls) == 21
    clock[0] = 160.01
    assert service.page(owner_sub=OWNER, limit=2)["status"] == "ok"
    assert len(provider.calls) == 22


def test_pagination_and_exact_lookup_cannot_bypass_each_others_provider_budget():
    from guardoath.admin_users import CognitoLookupRateLimited
    class Mixed(Fake):
        def list_users(self, **kwargs):
            self.calls.append(kwargs)
            return {"Users": []}
    provider = Mixed()
    service = directory(provider)
    for _ in range(10):
        service.page(owner_sub=OWNER)
    for _ in range(10):
        service.lookup(owner_sub=OWNER, email="someone@example.com")
    with pytest.raises(CognitoLookupRateLimited):
        service.page(owner_sub=OWNER)
    with pytest.raises(CognitoLookupRateLimited):
        service.lookup(owner_sub=OWNER, email="someone@example.com")
    assert len(provider.calls) == 20


def test_rejected_page_cursor_does_not_spend_directory_quota_or_call_provider():
    from guardoath.admin_users import CognitoLookupRateLimited
    provider = Fake()
    service = directory(provider)
    cursor = service.page(owner_sub=OWNER, limit=3)["next_cursor"]
    with pytest.raises(InvalidUserCursor):
        service.page(owner_sub=OWNER, limit=3, cursor=cursor + "corrupt")
    for _ in range(19):
        service.page(owner_sub=OWNER, limit=3)
    with pytest.raises(CognitoLookupRateLimited):
        service.page(owner_sub=OWNER, limit=3)
    assert len(provider.calls) == 20


@pytest.mark.parametrize("bad_response", [
    {"Users": "provider-private-value"},
    {"Users": [{"Username": "account", "UserStatus": {"secret": "do-not-leak"}, "Attributes": []}]},
    {"Users": [{"Username": "account", "Attributes": ["provider-private-value"]}]},
    {"Users": [], "PaginationToken": ["provider-private-value"]},
    {"Users": [{"Username": "account", "Attributes": []}] * 51},
])
def test_malformed_cognito_pages_are_provider_failures_not_owner_input_errors(bad_response):
    class Corrupt(Fake):
        def list_users(self, **kwargs):
            self.calls.append(kwargs)
            return bad_response
    provider = Corrupt()
    with pytest.raises(CognitoDirectoryUnavailable, match="Invalid Cognito directory response") as raised:
        directory(provider).page(owner_sub=OWNER, limit=50)
    assert "provider-private-value" not in str(raised.value)
    assert "do-not-leak" not in str(raised.value)
    assert len(provider.calls) == 1


def test_cognito_listing_exception_is_sanitized_provider_failure():
    class Offline(Fake):
        def list_users(self, **kwargs):
            self.calls.append(kwargs)
            raise RuntimeError("provider-private-value")
    provider = Offline()
    with pytest.raises(CognitoDirectoryUnavailable, match="Cognito directory provider unavailable") as raised:
        directory(provider).page(owner_sub=OWNER)
    assert "provider-private-value" not in str(raised.value)
    assert len(provider.calls) == 1


def test_authenticated_cognito_directory_api_distinguishes_provider_503_from_bad_query_422():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from guardoath.admin_console import VerifiedAdmin, create_admin_router

    class Corrupt(Fake):
        def list_users(self, **kwargs):
            self.calls.append(kwargs)
            return {"Users": [{"Username": "account", "UserStatus": [], "Attributes": []}]}

    provider = Corrupt()
    app = FastAPI()
    app.include_router(create_admin_router(
        verify_cognito_admin=lambda token: VerifiedAdmin(OWNER, frozenset({"owner"}), True)
        if token == "verified-owner" else None,
        admin_user_directory=directory(provider)))
    client = TestClient(app)
    route = "/v1/admin/users"
    assert client.get(route).status_code == 401
    assert client.get(route, headers={"X-GuardOath-Key": "verified-owner"}).status_code == 401
    assert len(provider.calls) == 0
    headers = {"X-GuardOath-Admin-Session": "verified-owner"}
    invalid = client.get(route + "?query=invalid%22", headers=headers)
    assert invalid.status_code == 422 and len(provider.calls) == 0
    unavailable = client.get(route, headers=headers)
    assert unavailable.status_code == 503 and len(provider.calls) == 1
    assert "provider-private" not in unavailable.text
    assert "Cognito user directory unavailable" in unavailable.text
