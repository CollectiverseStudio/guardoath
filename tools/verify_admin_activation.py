"""Read-only GuardOath admin infrastructure readiness report.

No mutations, token issuance, group membership changes, login attempts or
secret values. This is a configuration inventory, NOT MFA authentication proof
or permission to activate the admin console.
"""
from __future__ import annotations

import argparse
import json
import sys


def inspect(cfn, cognito, lam, *, stack_name="guardoath-site"):
    """Return only safe booleans and a stable explanation; never return identifiers."""
    result = {
        "stack_supports_admin_parameter": False,
        "stack_admin_enabled": False,
        "admin_oauth_client_provisioned": False,
        "admin_owner_group_provisioned": False,
        "lambda_admin_enabled": False,
        "registration_preserved": False,
        "cognito_mfa_required": False,
        "classic_hosted_ui_mfa_proof_available": False,
        "signed_current_login_mfa_verified": False,
        "ready_for_admin_access": False,
        "note": "A real signed current-login MFA claim must be verified; classic hosted UI defaults are not evidence.",
    }
    try:
        stack = cfn.describe_stacks(StackName=stack_name)["Stacks"][0]
        params = {p["ParameterKey"]: p.get("ParameterValue")
                  for p in stack.get("Parameters", ())}
        outputs = {p["OutputKey"]: p.get("OutputValue")
                   for p in stack.get("Outputs", ())}
        result["stack_supports_admin_parameter"] = "EnableAdminConsole" in params
        result["stack_admin_enabled"] = params.get("EnableAdminConsole") == "1"
        result["admin_oauth_client_provisioned"] = (
            result["stack_admin_enabled"] and bool(outputs.get("AdminClientId")))
        config = lam.get_function_configuration(
            FunctionName=outputs["FunctionName"])
        env = config.get("Environment", {}).get("Variables", {})
        result["lambda_admin_enabled"] = env.get("GUARDOATH_ADMIN_ENABLED") == "1"
        result["registration_preserved"] = (
            params.get("EnableSelfRegistration") == "1"
            and env.get("GUARDOATH_SELF_REGISTRATION") == "1")
        pool_id = outputs["UserPoolId"]
        pool = cognito.describe_user_pool(UserPoolId=pool_id)["UserPool"]
        result["cognito_mfa_required"] = pool.get("MfaConfiguration") == "ON"
        if result["stack_admin_enabled"]:
            try:
                group = cognito.get_group(
                    UserPoolId=pool_id, GroupName="GuardOathOwners")["Group"]
                result["admin_owner_group_provisioned"] = (
                    group.get("GroupName") == "GuardOathOwners")
            except Exception:
                # IAM outage or missing group: deny readiness without leaking details.
                pass
    except Exception:
        # Never expose AWS identifiers, secrets, provider exception bodies or user data.
        pass
    # The current template uses Cognito Lite/classic hosted UI. AWS issues
    # default ACR/AMR for classic UI even after MFA; only a real signed recent
    # authentication event from a compatible entry point can prove MFA.
    # This preflight never authenticates a user and must never issue approval.
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only GuardOath admin infrastructure inventory")
    parser.add_argument("--profile", default="collectiverse-prod")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--stack", default="guardoath-site")
    args = parser.parse_args(argv)
    try:
        import boto3
        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        result = inspect(
            session.client("cloudformation"), session.client("cognito-idp"),
            session.client("lambda"), stack_name=args.stack)
    except Exception:
        result = inspect(None, None, None, stack_name=args.stack)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ready_for_admin_access"] else 2


if __name__ == "__main__":
    sys.exit(main())
