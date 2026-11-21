"""Security and negative acceptance for Cognito Lite software-token admin MFA."""
import base64
import json

import pytest

from guardoath.admin_native_mfa import CognitoNativeMfa, NativeMfaUnavailable


class Cognito:
    def __init__(self):
        self.calls = []
        self.first = {"ChallengeName": "SOFTWARE_TOKEN_MFA",
                      "Session": "s" * 40,
                      "ChallengeParameters": {"USERNAME": "operator"}}
        self.second = {"AuthenticationResult": {
            "AccessToken": "a" * 40, "RefreshToken": "private-refresh"}}

    def initiate_auth(self, **kw):
        self.calls.append(("begin", kw))
        return self.first

    def respond_to_auth_challenge(self, **kw):
        self.calls.append(("finish", kw))
        return self.second


def handler(provider=None, at=1000, key=b"k" * 32):
    return CognitoNativeMfa(provider or Cognito(), "adminclient", key,
                            wallclock=lambda: at, clock=lambda: at)


def test_only_explicit_cognito_software_token_challenge_gets_ticket():
    service = Cognito()
    auth = handler(service)
    ticket = auth.begin("owner@example.com", "long-private-password")
    assert ticket is not None
    assert "long-private-password" not in ticket
    assert service.calls[0][1]["AuthFlow"] == "USER_PASSWORD_AUTH"
    assert service.calls[0][1]["ClientId"] == "adminclient"
    token = auth.finish(ticket, "123456")
    assert token == "a" * 40
    assert len(service.calls) == 2
    assert service.calls[1][1]["ChallengeName"] == "SOFTWARE_TOKEN_MFA"
    assert service.calls[1][1]["ChallengeResponses"] == {
        "USERNAME": "operator", "SOFTWARE_TOKEN_MFA_CODE": "123456"}
    assert service.second["AuthenticationResult"]["RefreshToken"] not in token


@pytest.mark.parametrize("alternative", [
    {"AuthenticationResult": {"AccessToken": "a"*40}},
    {"ChallengeName": "SMS_MFA", "Session": "s"*40},
    {"ChallengeName": "MFA_SETUP", "Session": "s"*40},
    {"ChallengeName": "NEW_PASSWORD_REQUIRED", "Session": "s"*40},
    {"ChallengeName": "SELECT_MFA_TYPE", "Session": "s"*40},
    {"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "s"*5},
    {},
])
def test_non_software_token_or_password_only_sign_in_never_grants_token(alternative):
    c = Cognito()
    c.first = alternative
    assert handler(c).begin("owner@example.com", "long-private-password") is None


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "１２３４５６", "", None, True])
def test_otp_format_is_strict_and_rejected_before_provider_call(code):
    c = Cognito()
    a = handler(c)
    ticket = a.begin("owner@example.com", "long-private-password")
    assert a.finish(ticket, code) is None
    assert len(c.calls) == 1


def test_ticket_signature_expiry_and_client_binding_are_strict():
    c = Cognito()
    a = handler(c)
    ticket = a.begin("owner@example.com", "long-private-password")
    assert a.finish(ticket[:-1] + ("A" if ticket[-1] != "A" else "B"), "123456") is None
    assert handler(c, at=1181).finish(ticket, "123456") is None
    assert handler(c, at=960).finish(ticket, "123456") is None
    assert handler(c, key=b"z"*32).finish(ticket, "123456") is None
    assert len(c.calls) == 1
    assert a.finish(ticket, "123456") == "a"*40


def test_invalid_identifiers_and_password_do_not_reach_provider():
    c = Cognito()
    a = handler(c)
    for email, password in [("invalid", "long-private-password"),
                            ("owner@example.com", "short"),
                            ("a"*255+"@example.com", "long-private-password")]:
        assert a.begin(email, password) is None
    assert c.calls == []


def test_second_challenge_or_missing_result_never_exposes_access():
    c = Cognito()
    a = handler(c)
    t = a.begin("owner@example.com", "long-private-password")
    for bad in [{"ChallengeName": "SOFTWARE_TOKEN_MFA", "Session": "s"*40},
                {"AuthenticationResult": {"IdToken": "other"}},
                {"AuthenticationResult": {"AccessToken": 123}}]:
        c.second = bad
        assert a.finish(t, "123456") is None


def test_provider_error_and_rate_exhaustion_fail_closed():
    class Broken(Cognito):
        def initiate_auth(self, **kw):
            raise RuntimeError("private provider exception")
    with pytest.raises(NativeMfaUnavailable):
        handler(Broken()).begin("owner@example.com", "long-private-password")
    c = Cognito()
    a = handler(c)
    for _ in range(12):
        assert a.begin("owner@example.com", "long-private-password") is not None
    with pytest.raises(NativeMfaUnavailable):
        a.begin("owner@example.com", "long-private-password")
    assert len(c.calls) == 12


def test_ticket_malformed_json_and_unknown_fields_fail_before_cognito():
    c = Cognito()
    a = handler(c)
    t = a.begin("owner@example.com", "long-private-password")
    payload = json.loads(base64.urlsafe_b64decode(t.split(".")[0] + "=="))
    assert payload["client"] == "adminclient"
    assert "password" not in payload
    assert a.finish(t + "extra", "123456") is None
    assert a.finish("not-signed", "123456") is None
    assert len(c.calls) == 1
