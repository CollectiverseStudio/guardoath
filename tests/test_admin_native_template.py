"""GuardOath admin native MFA is opt-in and cannot replace owner authorization."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_admin_flow_requires_separate_admin_client_and_explicit_enable():
    template = json.loads((ROOT / "deploy/aws/guardoath-site.json").read_text())
    params = template["Parameters"]
    assert params["EnableAdminNativeMfa"]["Default"] == "0"
    assert params["EnableAdminConsole"]["Default"] == "0"
    assert template["Rules"]["AdminNativeMfaRequiresConsole"]["Assertions"]
    condition = template["Conditions"]["AdminNativeMfaEnabled"]
    assert condition == {"Fn::Equals": [{"Ref": "EnableAdminNativeMfa"}, "1"]}
    native = template["Resources"]["AdminUserPoolClient"]
    assert native["Condition"] == "AdminConsoleEnabled"
    assert native["Properties"]["GenerateSecret"] is False
    assert native["Properties"]["CallbackURLs"] == [{"Fn::Sub": "https://${DomainName}/admin/login"}]
    assert native["Properties"]["ExplicitAuthFlows"] == [
        "ALLOW_REFRESH_TOKEN_AUTH",
        {"Fn::If": ["AdminNativeMfaEnabled", "ALLOW_USER_PASSWORD_AUTH",
                    {"Ref": "AWS::NoValue"}]},
    ]
    customer = template["Resources"]["UserPoolClient"]
    assert customer["Properties"]["ExplicitAuthFlows"] == ["ALLOW_REFRESH_TOKEN_AUTH"]
    env = template["Resources"]["SiteFunction"]["Properties"]["Environment"]["Variables"]
    assert env["GUARDOATH_ADMIN_NATIVE_MFA_ENABLED"] == {"Ref": "EnableAdminNativeMfa"}
    assert env["GUARDOATH_ADMIN_ENABLED"] == {"Ref": "EnableAdminConsole"}
    pool = template["Resources"]["UserPool"]["Properties"]
    assert pool["UserPoolTier"] == "LITE"
    assert pool["MfaConfiguration"] == "ON"
    assert pool["EnabledMfas"] == ["SOFTWARE_TOKEN_MFA"]
