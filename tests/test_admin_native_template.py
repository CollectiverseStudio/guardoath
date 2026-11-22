"""GuardOath admin native MFA is opt-in and cannot replace owner authorization."""
import json
from pathlib import Path
import re

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


def test_first_enrollment_requires_one_explicit_subject_and_all_existing_safeguards():
    template = json.loads((ROOT / "deploy/aws/guardoath-site.json").read_text())
    grant = template["Parameters"]["AdminEnrollmentSubject"]
    assert grant["Default"] == "" and grant["NoEcho"] is True and grant["MaxLength"] == 36
    pattern = grant["AllowedPattern"]
    assert re.fullmatch(pattern, "")
    assert re.fullmatch(pattern, "abcdefab-cdef-abcd-efab-cdefabcdefab")
    for denied in ("owner@example.invalid", "*", " ", "not-a-subject"):
        assert not re.fullmatch(pattern, denied)
    rule = template["Rules"]["AdminEnrollmentRequiresNativeAudit"]
    assert rule["RuleCondition"] == {"Fn::Not": [{"Fn::Equals": [{"Ref": "AdminEnrollmentSubject"}, ""]}]}
    assert rule["Assertions"][0]["Assert"] == {"Fn::And": [
        {"Fn::Equals": [{"Ref": name}, "1"]} for name in
        ("EnableAdminConsole", "EnableAdminNativeMfa", "EnableAdminMutations")]}
    assert template["Conditions"]["AdminEnrollmentEnabled"] == {"Fn::And": [
        {"Condition": "AdminConsoleEnabled"}, {"Condition": "AdminNativeMfaEnabled"},
        {"Condition": "AdminMutationsEnabled"},
        {"Fn::Not": [{"Fn::Equals": [{"Ref": "AdminEnrollmentSubject"}, ""]}]}]}
    env = template["Resources"]["SiteFunction"]["Properties"]["Environment"]["Variables"]
    assert env["GUARDOATH_ADMIN_ENROLLMENT_SUBJECT"] == {
        "Fn::If": ["AdminEnrollmentEnabled", {"Ref": "AdminEnrollmentSubject"}, {"Ref": "AWS::NoValue"}]}
