"""The website deployment template (deploy/aws/guardoath-site.json) and its build script.

These check the parts that keep the site free and working: CloudFront's free plan covers the
distribution, the web ACL and the hosted zone; CloudFront reaches Lambda only through origin access
control; the alarms stop the site before Lambda's free allowances run out; and nothing in the stack is
a service without an always-free allowance.
"""
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_FILE = ROOT / "deploy" / "aws" / "guardoath-site.json"
FREE_REQUESTS, FREE_GB_SECONDS, HOURS, DAYS = 1_000_000, 400_000, 24 * 31, 31
# CloudFront managed policy IDs, copied from AWS's lists (checked 2026-10-30):
# docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-cache-policies.html
# docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html
# The first deploy failed because CachingDisabled was mistyped (9df4 for 9df3) in both the template and here.
MANAGED = {"CachingDisabled": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
           "CachingOptimized": "658327ea-f89d-4fab-a63d-7e88639e58f6",
           "AllViewerExceptHostHeader": "b689b0a8-53d0-40ab-baf2-68738e2966ac"}


@pytest.fixture(scope="module")
def template():
    return json.loads(TEMPLATE_FILE.read_text(encoding="utf-8"))


def resources(template, kind):
    return {name: r["Properties"] for name, r in template["Resources"].items() if r["Type"] == kind}


def default(template, name):
    return template["Parameters"][name]["Default"]


def test_the_template_can_be_deployed_without_an_s3_bucket():
    # aws cloudformation deploy sends a template of up to 51,200 bytes in the request itself.
    assert len(TEMPLATE_FILE.read_bytes()) < 51_200


def test_only_services_with_a_free_allowance_or_covered_by_the_free_plan_are_used(template):
    kinds = {r["Type"] for r in template["Resources"].values()}
    assert kinds == {"AWS::DynamoDB::Table", "AWS::Logs::LogGroup", "AWS::IAM::Role", "AWS::Lambda::Function",
                     "AWS::Lambda::Url", "AWS::Lambda::Permission", "AWS::CloudFront::OriginAccessControl",
                     "AWS::CertificateManager::Certificate", "AWS::WAFv2::WebACL", "AWS::CloudFront::Distribution",
                     "AWS::Route53::RecordSet", "AWS::PricingPlanManager::Subscription", "AWS::CloudWatch::Alarm"}


def test_the_free_plan_covers_the_distribution_web_acl_and_hosted_zone(template):
    (plan,) = resources(template, "AWS::PricingPlanManager::Subscription").values()
    assert (plan["PlanFamily"], plan["PlanTier"], plan["UsageLevel"]) == ("CloudFront", "FREE", "DEFAULT")
    arns = json.dumps(plan["ResourceArns"])
    assert ":distribution/${Distribution}" in arns and '"WebAcl"' in arns and "hostedzone/${HostedZoneId}" in arns
    (acl,) = resources(template, "AWS::WAFv2::WebACL").values()
    assert acl["Scope"] == "CLOUDFRONT" and len(acl["Rules"]) <= 5
    assert template["Rules"]["UsEast1Only"]["Assertions"][0]["Assert"] == {"Fn::Equals": [{"Ref": "AWS::Region"}, "us-east-1"]}


def test_each_visitor_address_is_rate_limited(template):
    (acl,) = resources(template, "AWS::WAFv2::WebACL").values()
    (rule,) = acl["Rules"]
    rate = rule["Statement"]["RateBasedStatement"]
    assert rule["Action"] == {"Block": {}} and rate["AggregateKeyType"] == "IP" and rate["EvaluationWindowSec"] == 300
    assert rate["Limit"] == {"Ref": "RequestsPerIpPer5Minutes"}


def test_cloudfront_reaches_lambda_only_through_origin_access_control(template):
    (url,) = resources(template, "AWS::Lambda::Url").values()
    assert url["AuthType"] == "AWS_IAM" and url["InvokeMode"] == "BUFFERED"
    (oac,) = resources(template, "AWS::CloudFront::OriginAccessControl").values()
    assert oac["OriginAccessControlConfig"]["OriginAccessControlOriginType"] == "lambda"
    assert oac["OriginAccessControlConfig"]["SigningBehavior"] == "always"
    (dist,) = resources(template, "AWS::CloudFront::Distribution").values()
    (origin,) = dist["DistributionConfig"]["Origins"]
    assert origin["OriginAccessControlId"] == {"Fn::GetAtt": ["OriginAccess", "Id"]}
    permissions = [p for p in resources(template, "AWS::Lambda::Permission").values() if p["Principal"] == "cloudfront.amazonaws.com"]
    # CloudFront needs both actions, each limited to this distribution.
    assert sorted(p["Action"] for p in permissions) == ["lambda:InvokeFunction", "lambda:InvokeFunctionUrl"]
    assert all(":distribution/${Distribution}" in p["SourceArn"]["Fn::Sub"] for p in permissions)


def test_api_requests_are_not_cached_and_reach_the_app_with_their_headers(template):
    config = resources(template, "AWS::CloudFront::Distribution")["Distribution"]["DistributionConfig"]
    behavior = config["DefaultCacheBehavior"]
    assert behavior["CachePolicyId"] == MANAGED["CachingDisabled"]
    assert behavior["OriginRequestPolicyId"] == MANAGED["AllViewerExceptHostHeader"]
    assert "POST" in behavior["AllowedMethods"] and behavior["ViewerProtocolPolicy"] == "redirect-to-https"
    (assets,) = config["CacheBehaviors"]
    assert assets["PathPattern"] == "/assets/*" and assets["CachePolicyId"] == MANAGED["CachingOptimized"]
    assert assets["AllowedMethods"] == ["GET", "HEAD"]


def test_the_site_answers_on_the_domain_and_www_over_https(template):
    config = resources(template, "AWS::CloudFront::Distribution")["Distribution"]["DistributionConfig"]
    assert config["Aliases"] == [{"Ref": "DomainName"}, {"Fn::Sub": "www.${DomainName}"}]
    assert config["ViewerCertificate"]["SslSupportMethod"] == "sni-only"
    records = resources(template, "AWS::Route53::RecordSet").values()
    assert sorted(r["Type"] for r in records) == ["A", "A", "AAAA", "AAAA"]
    assert all(r["AliasTarget"]["HostedZoneId"] == "Z2FDTNDATAQYW2" for r in records)
    (certificate,) = resources(template, "AWS::CertificateManager::Certificate").values()
    assert certificate["ValidationMethod"] == "DNS" and len(certificate["DomainValidationOptions"]) == 2


def test_the_site_function_runs_the_hosted_app(template):
    function = resources(template, "AWS::Lambda::Function")["SiteFunction"]
    assert function["Handler"] == "index.handler" and function["Runtime"] == "python3.13"
    assert function["ReservedConcurrentExecutions"] == 2
    env = function["Environment"]["Variables"]
    assert env["GUARDOATH_TENANT_STORE"] == "dynamodb" and env["GUARDOATH_TENANT_TABLE"] == {"Ref": "TenantTable"}
    assert env["GUARDOATH_DEMO_KEY_SECRET"] == {"Ref": "DemoKeySecret"} and template["Parameters"]["DemoKeySecret"]["NoEcho"] is True
    assert env["GUARDOATH_CONTENT_SAFETY_PROVIDER"] == "none" and env["GUARDOATH_ENABLE_AZURE_PROMPT_SHIELDS"] == "0"
    assert "GUARDOATH_API_KEY" not in env


def test_the_site_role_can_use_only_its_table_and_log_group(template):
    statements = resources(template, "AWS::IAM::Role")["SiteRole"]["Policies"][0]["PolicyDocument"]["Statement"]
    assert statements[1]["Action"] == ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:Scan"]
    assert statements[1]["Resource"] == {"Fn::GetAtt": ["TenantTable", "Arn"]}
    assert all("*" not in json.dumps(s["Resource"]) for s in statements)


def test_the_table_stays_within_the_free_capacity(template):
    (table,) = resources(template, "AWS::DynamoDB::Table").values()
    assert table["BillingMode"] == "PROVISIONED"
    for name in ("TableReadCapacity", "TableWriteCapacity"):
        assert template["Parameters"][name]["MaxValue"] <= 25 and default(template, name) <= 25
    assert set(table) == {"TableName", "BillingMode", "AttributeDefinitions", "KeySchema",
                          "ProvisionedThroughput", "DeletionProtectionEnabled"}  # no paid add-ons


def test_the_alarms_stop_the_site_before_the_free_lambda_allowances_run_out(template):
    alarms = resources(template, "AWS::CloudWatch::Alarm")
    by_metric = {(a["MetricName"], a["Period"]): a for a in alarms.values()}
    assert default(template, "InvocationsPerHourLimit") * HOURS < FREE_REQUESTS
    assert default(template, "InvocationsPerDayLimit") * DAYS < FREE_REQUESTS
    memory_gb = resources(template, "AWS::Lambda::Function")["SiteFunction"]["MemorySize"] / 1024
    assert default(template, "DurationMsPerHourLimit") / 1000 * HOURS * memory_gb < FREE_GB_SECONDS
    assert set(by_metric) == {("Invocations", 3600), ("Invocations", 86400), ("Duration", 3600)}
    for alarm in alarms.values():
        assert alarm["Namespace"] == "AWS/Lambda" and alarm["AlarmActions"] == [{"Fn::GetAtt": ["ShutoffFunction", "Arn"]}]
        assert alarm["Statistic"] == "Sum" and alarm["Period"] * alarm["EvaluationPeriods"] <= 86400
        assert alarm["AlarmName"].startswith("guardoath-site-")


def test_the_shutoff_sets_the_site_concurrency_to_zero_and_nothing_else(template):
    code = resources(template, "AWS::Lambda::Function")["ShutoffFunction"]["Code"]["ZipFile"]
    assert "put_function_concurrency" in code and "ReservedConcurrentExecutions=0" in code and '!= "ALARM"' in code
    statements = resources(template, "AWS::IAM::Role")["ShutoffRole"]["Policies"][0]["PolicyDocument"]["Statement"]
    assert statements[1] == {"Effect": "Allow", "Action": "lambda:PutFunctionConcurrency", "Resource": {"Fn::GetAtt": ["SiteFunction", "Arn"]}}
    (permission,) = [p for p in resources(template, "AWS::Lambda::Permission").values() if p["Principal"] == "lambda.alarms.cloudwatch.amazonaws.com"]
    assert permission["SourceAccount"] == {"Ref": "AWS::AccountId"} and permission["SourceArn"]["Fn::Sub"].endswith(":alarm:guardoath-site-*")


def load_inline(template, name, tmp_path):
    path = tmp_path / (name + ".py")
    path.write_text(resources(template, "AWS::Lambda::Function")[name]["Code"]["ZipFile"], encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_shutoff_acts_only_on_an_alarm(monkeypatch, template, tmp_path):
    calls = []
    fake = types.SimpleNamespace(client=lambda name: types.SimpleNamespace(put_function_concurrency=lambda **kw: calls.append(kw)))
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.setenv("SITE_FUNCTION", "guardoath-site")
    shutoff = load_inline(template, "ShutoffFunction", tmp_path)
    assert shutoff.handler({"alarmData": {"state": {"value": "OK"}}}, None) == {"changed": False, "state": "OK"}
    assert calls == []
    assert shutoff.handler({"alarmData": {"alarmName": "guardoath-site-x", "state": {"value": "ALARM"}}}, None) == {"changed": True}
    assert calls == [{"FunctionName": "guardoath-site", "ReservedConcurrentExecutions": 0}]


def test_the_placeholder_says_the_site_is_being_deployed(template, tmp_path):
    response = load_inline(template, "SiteFunction", tmp_path).handler({}, None)
    assert response["statusCode"] == 503 and "being deployed" in response["body"]


def test_log_groups_keep_one_week(template):
    assert all(group["RetentionInDays"] == 7 for group in resources(template, "AWS::Logs::LogGroup").values())


def test_inline_code_fits_the_inline_limit(template):
    for function in resources(template, "AWS::Lambda::Function").values():
        assert len(function["Code"]["ZipFile"].encode()) <= 4096


# The build script


def test_the_build_bundles_the_dependencies_the_hosted_site_needs():
    spec = importlib.util.spec_from_file_location("build_lambda", ROOT / "deploy" / "aws" / "build_lambda.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    names = {r.split("==")[0] for r in build.requirements()}
    assert {"fastapi", "pydantic", "tldextract", "phonenumbers", "boto3", "botocore"} <= names
    assert all("==" in r for r in build.requirements())
    assert build.INDEX.startswith("from guardoath.lambda_handler import handler")
    assert build.DIRECT_UPLOAD_LIMIT == 50 * 1024 * 1024 and build.UNZIPPED_LIMIT == 250 * 1024 * 1024


def test_every_policy_id_in_the_template_is_a_documented_managed_policy():
    import re
    ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", TEMPLATE_FILE.read_text(encoding="utf-8")))
    assert ids == set(MANAGED.values())
