"""Read-only Google/Cognito deployment verification without live AWS credentials."""
import json
from types import SimpleNamespace

from tools.verify_google_registration import CHECKS, verify


DOMAIN = "https://guardoath.com"
POOL = "us-east-1_TestPool"
CLIENT = "testclientwithoutsecret"
IDP = "guardoath-sign-in.auth.us-east-1.amazoncognito.com"
FUNCTION = "guardoath-site"


class CloudFormation:
    def __init__(self, google="true", registration="1"):
        self.google, self.registration = google, registration

    def describe_stacks(self, *, StackName):
        assert StackName == "guardoath-site"
        return {"Stacks": [{"Outputs": [
            {"OutputKey": name, "OutputValue": value}
            for name, value in {
                "GoogleLoginConfigured": self.google,
                "SelfRegistrationConfigured": self.registration,
                "UserPoolId": POOL, "UserPoolClientId": CLIENT,
                "FunctionName": FUNCTION, "SignInDomain": IDP,
            }.items()
        ]}]}


class Cognito:
    def __init__(self, *, email_verified=True, client_secret=None):
        self.email_verified, self.client_secret = email_verified, client_secret

    def describe_identity_provider(self, *, UserPoolId, ProviderName):
        assert UserPoolId == POOL and ProviderName == "Google"
        return {"IdentityProvider": {
            "ProviderName": "Google", "ProviderType": "Google",
            "ProviderDetails": {
                "client_id": "PRIVATE_GOOGLE_OAUTH_CLIENT_ID",
                "client_secret": "PRIVATE_GOOGLE_OAUTH_SECRET",
                "authorize_scopes": "email profile openid",
            },
            "AttributeMapping": {
                "email": "email",
                "email_verified": "email_verified" if self.email_verified else "",
            },
        }}

    def describe_user_pool_client(self, *, UserPoolId, ClientId):
        assert UserPoolId == POOL and ClientId == CLIENT
        return {"UserPoolClient": {
            "SupportedIdentityProviders": ["COGNITO", "Google"],
            "CallbackURLs": [DOMAIN + "/dashboard"],
            "LogoutURLs": [DOMAIN + "/dashboard"],
            "AllowedOAuthFlows": ["code"],
            "AllowedOAuthScopes": ["openid", "email", "aws.cognito.signin.user.admin"],
            "ClientSecret": self.client_secret,
        }}


class Lambda:
    def __init__(self, google="1", registration="1"):
        self.google, self.registration = google, registration

    def get_function_configuration(self, *, FunctionName):
        assert FunctionName == FUNCTION
        return {"Environment": {"Variables": {
            "GUARDOATH_GOOGLE_LOGIN_ENABLED": self.google,
            "GUARDOATH_SELF_REGISTRATION": self.registration,
            "PRIVATE_ENV_SECRET": "DO_NOT_REPORT_ANY_SECRET_VALUE",
        }}}


def public(url):
    assert url == DOMAIN + "/v1/sign-in"
    return {
        "google_provider": "Google",
        "registration_enabled": True,
        "authorize_url": "https://" + IDP + "/oauth2/authorize",
        "redirect_uri": DOMAIN + "/dashboard",
        "client_id": CLIENT,
        "PRIVATE_RESPONSE_VALUE": "NEVER_ECHO_THIS",
    }


def test_google_registration_deployment_preflight_requires_all_layers():
    result = verify(CloudFormation(), Cognito(), Lambda(), get_public=public)
    assert result["ready_for_live_google_browser_test"] is True
    assert all(result[k] is True for k in CHECKS)
    encoded = json.dumps(result)
    for private in ("PRIVATE_", "DO_NOT_REPORT", "NEVER_ECHO", POOL, CLIENT, IDP):
        assert private not in encoded
    assert "live Google account roundtrip not tested" in encoded


def test_unconfigured_or_mismatched_stack_can_never_be_declared_ready():
    cases = [
        (CloudFormation(google="false"), Cognito(), Lambda()),
        (CloudFormation(registration="0"), Cognito(), Lambda()),
        (CloudFormation(), Cognito(email_verified=False), Lambda()),
        (CloudFormation(), Cognito(client_secret="UNEXPECTED_CLIENT_SECRET"), Lambda()),
        (CloudFormation(), Cognito(), Lambda(google="0")),
        (CloudFormation(), Cognito(), Lambda(registration="0")),
    ]
    for stack, provider, function in cases:
        result = verify(stack, provider, function, get_public=public)
        assert result["ready_for_live_google_browser_test"] is False
        assert "UNEXPECTED_CLIENT_SECRET" not in json.dumps(result)


def test_public_google_configuration_cannot_claim_readiness_if_wrong():
    def missing_google(url):
        return {**public(url), "google_provider": None}
    assert not verify(CloudFormation(), Cognito(), Lambda(),
                      get_public=missing_google)["ready_for_live_google_browser_test"]

    def wrong_callback(url):
        return {**public(url), "redirect_uri": "https://wrong.example/dashboard"}
    assert not verify(CloudFormation(), Cognito(), Lambda(),
                      get_public=wrong_callback)["ready_for_live_google_browser_test"]


def test_unreachable_aws_is_source_free_and_incomplete():
    class Inaccessible:
        def describe_stacks(self, **kwargs):
            raise RuntimeError("PRIVATE_CREDENTIALS_ARE_NOT_OUTPUT")
    result = verify(Inaccessible(), Cognito(), Lambda(), get_public=public)
    assert result["ready_for_live_google_browser_test"] is False
    assert not any(result[k] for k in CHECKS)
    assert "PRIVATE_" not in json.dumps(result)
