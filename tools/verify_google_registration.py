"""Read-only production Google/Cognito registration configuration gate.

Exit 0 only if the deployed stack, Cognito client/provider, Lambda environment
and public sign-in endpoint agree. This does NOT authenticate a real Google user
or establish paid entitlement; live browser registration must be separately tested.
Never print identities, OAuth client IDs or secrets.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request


CHECKS = (
    "google_stack_enabled",
    "registration_stack_enabled",
    "google_idp",
    "email_verified_mapping",
    "cognito_client_google",
    "cognito_callback",
    "cognito_scopes",
    "lambda_google_enabled",
    "lambda_registration_enabled",
    "public_google_sign_in",
    "public_registration_enabled",
    "public_callback",
    "public_client_binding",
)


def verify(cfn, cognito, aws_lambda, *, stack_name="guardoath-site",
           domain="https://guardoath.com", get_public=None):
    """Only predefined boolean fields leave this function."""
    result = {name: False for name in CHECKS}
    result["scope"] = "deployment configuration only; live Google account roundtrip not tested"
    get_public = get_public or _get_public

    # Even a single missing/inaccessible AWS/HTTP requirement makes readiness false.
    try:
        stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
        outputs = {item["OutputKey"]: item["OutputValue"]
                   for item in stack.get("Outputs", ())}
        result["google_stack_enabled"] = outputs.get("GoogleLoginConfigured") == "true"
        result["registration_stack_enabled"] = outputs.get("SelfRegistrationConfigured") == "1"
        pool_id = outputs["UserPoolId"]
        client_id = outputs["UserPoolClientId"]
        func = outputs["FunctionName"]
        sign_in_domain = outputs["SignInDomain"]

        provider = cognito.describe_identity_provider(
            UserPoolId=pool_id, ProviderName="Google")["IdentityProvider"]
        result["google_idp"] = (
            provider.get("ProviderType") == "Google"
            and provider.get("ProviderName") == "Google"
            and bool(provider.get("ProviderDetails", {}).get("client_id"))
            and bool(provider.get("ProviderDetails", {}).get("authorize_scopes"))
        )
        mapping = provider.get("AttributeMapping", {})
        result["email_verified_mapping"] = (
            mapping.get("email") == "email"
            and mapping.get("email_verified") == "email_verified"
        )

        client = cognito.describe_user_pool_client(
            UserPoolId=pool_id, ClientId=client_id)["UserPoolClient"]
        result["cognito_client_google"] = (
            "Google" in client.get("SupportedIdentityProviders", ())
            and "COGNITO" in client.get("SupportedIdentityProviders", ())
            and not client.get("ClientSecret")
        )
        callback = domain.rstrip("/") + "/dashboard"
        result["cognito_callback"] = (
            callback in client.get("CallbackURLs", ())
            and callback in client.get("LogoutURLs", ())
        )
        result["cognito_scopes"] = (
            client.get("AllowedOAuthFlows") == ["code"]
            and {"openid", "email", "aws.cognito.signin.user.admin"}
            <= set(client.get("AllowedOAuthScopes", ()))
        )

        env = aws_lambda.get_function_configuration(
            FunctionName=func).get("Environment", {}).get("Variables", {})
        result["lambda_google_enabled"] = env.get("GUARDOATH_GOOGLE_LOGIN_ENABLED") == "1"
        result["lambda_registration_enabled"] = env.get("GUARDOATH_SELF_REGISTRATION") == "1"

        public = get_public(domain.rstrip("/") + "/v1/sign-in")
        result["public_google_sign_in"] = (
            public.get("google_provider") == "Google"
            and public.get("authorize_url") ==
            "https://" + sign_in_domain + "/oauth2/authorize"
        )
        result["public_registration_enabled"] = (
            public.get("registration_enabled") is True
        )
        result["public_callback"] = public.get("redirect_uri") == callback
        result["public_client_binding"] = public.get("client_id") == client_id
    except Exception:
        # Error details may contain credentials, tenant IDs or request context.
        # Keep them out of logs and console. False checks distinguish incomplete.
        pass

    result["ready_for_live_google_browser_test"] = all(result[k] for k in CHECKS)
    return result


def _get_public(url):
    with urllib.request.urlopen(url, timeout=8) as response:
        if response.status != 200:
            raise ValueError("non-success")
        raw = response.read(4096 + 1)
        if len(raw) > 4096:
            raise ValueError("oversized sign-in response")
        return json.loads(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only GuardOath Google login preflight")
    parser.add_argument("--profile", default="collectiverse-prod")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack", default="guardoath-site")
    parser.add_argument("--domain", default="https://guardoath.com")
    args = parser.parse_args(argv)
    try:
        import boto3
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        result = verify(
            session.client("cloudformation"), session.client("cognito-idp"),
            session.client("lambda"), stack_name=args.stack, domain=args.domain)
    except Exception:
        result = {key: False for key in CHECKS}
        result["scope"] = "deployment configuration only; live Google account roundtrip not tested"
        result["ready_for_live_google_browser_test"] = False
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready_for_live_google_browser_test"] else 2


if __name__ == "__main__":
    sys.exit(main())
