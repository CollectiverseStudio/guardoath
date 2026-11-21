"""Admin infrastructure preflight never invents verified MFA or expose secrets."""
from tools.verify_admin_activation import inspect


class CFN:
    def __init__(self, *, admin="0", registration="1", supports_admin=True):
        self.admin = admin
        self.registration = registration
        self.supports_admin = supports_admin

    def describe_stacks(self, *, StackName):
        params = [{"ParameterKey": "EnableSelfRegistration",
                   "ParameterValue": self.registration}]
        if self.supports_admin:
            params.append({"ParameterKey": "EnableAdminConsole",
                           "ParameterValue": self.admin})
        return {"Stacks": [{"Parameters": params, "Outputs": [
            {"OutputKey": "FunctionName", "OutputValue": "private-function"},
            {"OutputKey": "UserPoolId", "OutputValue": "private-pool"},
            {"OutputKey": "AdminClientId", "OutputValue": "private-admin-client"},
        ]}]}


class Lambda:
    def __init__(self, *, admin="0", registration="1"):
        self.admin = admin
        self.registration = registration

    def get_function_configuration(self, *, FunctionName):
        return {"Environment": {"Variables": {
            "GUARDOATH_ADMIN_ENABLED": self.admin,
            "GUARDOATH_SELF_REGISTRATION": self.registration,
            "UNRELATED_PRIVATE_SECRET": "must-never-appear",
        }}}


class Cognito:
    def describe_user_pool(self, *, UserPoolId):
        return {"UserPool": {"MfaConfiguration": "ON"}}

    def get_group(self, *, UserPoolId, GroupName):
        return {"Group": {"GroupName": GroupName}}


def test_old_stack_missing_admin_parameter_is_not_ready():
    result = inspect(CFN(supports_admin=False), Cognito(), Lambda())
    assert result["stack_supports_admin_parameter"] is False
    assert result["stack_admin_enabled"] is False
    assert result["registration_preserved"] is True
    assert result["ready_for_admin_access"] is False


def test_admin_enabled_never_fabricates_fresh_mfa_evidence():
    result = inspect(CFN(admin="1"), Cognito(), Lambda(admin="1"))
    assert result["admin_oauth_client_provisioned"] is True
    assert result["admin_owner_group_provisioned"] is True
    assert result["cognito_mfa_required"] is True
    assert result["signed_current_login_mfa_verified"] is False
    assert result["classic_hosted_ui_mfa_proof_available"] is False
    assert result["ready_for_admin_access"] is False
    assert "private-" not in str(result)
    assert "must-never-appear" not in str(result)


def test_registration_disabled_is_reported_without_changing_it():
    result = inspect(CFN(admin="1", registration="0"), Cognito(),
                     Lambda(admin="1", registration="0"))
    assert result["registration_preserved"] is False
    assert result["ready_for_admin_access"] is False


def test_provider_failure_is_not_falsely_approved():
    result = inspect(None, None, None)
    assert result["ready_for_admin_access"] is False
    assert result["admin_oauth_client_provisioned"] is False
