"""Fresh-process acceptance against an isolated copy of the real shipped app.

Only identity-provider and DynamoDB transports are synthetic. No source provider,
registry, tenant model, Engine, admin route or source-release writer is replaced.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from time import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--next-title")
    parser.add_argument("--initialize-store", action="store_true")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    for key in tuple(os.environ):
        if key.startswith("GUARDOATH_"):
            del os.environ[key]
    os.environ.update({"GUARDOATH_ADMIN_ENABLED": "0",
                       "GUARDOATH_INVOCATIONS_ENABLED": "0",
                       "GUARDOATH_TENANT_STORE": "memory",
                       "AWS_EC2_METADATA_DISABLED": "true"})

    import guardoath
    from guardoath import catalog_metadata, rule_catalog
    for module in (guardoath, catalog_metadata, rule_catalog):
        assert Path(module.__file__).resolve().is_relative_to(root / "guardoath")

    from fastapi.testclient import TestClient
    from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
    from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
    from guardoath.admin_catalog import published_catalog, read_catalog
    from guardoath.admin_catalog_drafts import AdminCatalogDrafts
    from guardoath.admin_catalog_history import verified_catalog_history
    from guardoath.admin_integration import mount_admin_console
    from guardoath.app import create_app
    from guardoath.dynamodb_store import DynamoDBTenantStore, to_item
    from guardoath.engine import Engine
    from guardoath.hosted import TenantService
    from guardoath.lexical_checker import lexical_guards_check
    from guardoath.models import CheckRequest, Stage
    from guardoath.tenant_record import TenantRecord, TENANT_POLICY_VERSION

    owner = "12345678-1234-1234-1234-123456789012"

    class Sessions:
        pool_id = "us-east-1_example"

        def verified_claims(self, token):
            if token == "synthetic-verified-owner":
                return {"sub": owner, "username": "operator",
                        "amr": ["pwd", "mfa"], "auth_time": int(time())}
            return None

    class Cognito:
        def get_user(self, *, AccessToken):
            assert AccessToken == "synthetic-verified-owner"
            return {"Username": "operator"}

        def admin_get_user(self, *, UserPoolId, Username):
            assert UserPoolId == Sessions.pool_id and Username == "operator"
            return {"Enabled": True, "UserStatus": "CONFIRMED",
                    "UserAttributes": [{"Name": "sub", "Value": owner}]}

        def admin_list_groups_for_user(self, *, UserPoolId, Username, Limit):
            assert UserPoolId == Sessions.pool_id and Username == "operator" and Limit == 60
            return {"Groups": [{"GroupName": "GuardOathOwners"}]}

    if args.initialize_store:
        assert not args.database.exists(), "Baseline requires a new isolated store"
    else:
        assert args.database.is_file(), "Persisted catalog acceptance store missing"
    db = AdminTestDynamo(args.database)
    store = DynamoDBTenantStore("tenant_table", db)
    configs = {
        "enabled": {},
        "rule-off": {"input_rule_actions": {"agent.git_force": "off"},
                     "output_rule_actions": {"agent.git_force": "block"}},
        "parent-off": {"input_check_lexical_guards": None,
                       "input_rule_actions": {"agent.git_force": "block"}},
    }
    for label, changes in configs.items():
        tenant_id = "usr-catalog-" + label
        existing = store.get(tenant_id)
        if args.initialize_store:
            assert existing is None
            record = TenantRecord.model_validate({
                "tenant_id": tenant_id, "display_name": "Synthetic catalog acceptance",
                "demo": False, "status": "active", "revision": 9,
                "subscription_status": "active", "billing_mode": "stripe",
                "payer_id": "cus_catalog", "stripe_subscription_id": "sub_catalog",
                "monthly_allowance": 4000, "overage": "block", "key_hashes": ["a" * 64],
                "usage": {"2026-11": 17}, "input_attachment_csv_mode": "off",
                "input_check_lexical_guards": "block", "output_check_lexical_guards": "block",
                **changes,
            })
            db.seed("tenant_table", to_item(record))
        else:
            assert existing is not None, "Persisted catalog acceptance tenant missing"

    def tenant_rows():
        return {label: db.get_item(TableName="tenant_table",
                  Key={"tenant_id": {"S": "usr-catalog-" + label}}) for label in configs}

    before = tenant_rows()
    checkers = {"lexical_guards": lexical_guards_check}
    tenants = TenantService(store, checkers)
    app = create_app(engine=Engine([], checkers), credentials={}, tenants=tenants)
    drafts = AdminCatalogDrafts(db, "audit_table", ADMIN_BROWSER_AUDIT_KEY)
    auditor = AdminSensitiveReadAuditor(db, "audit_table", signing_key=ADMIN_BROWSER_AUDIT_KEY)
    mount_admin_console(app, sessions=Sessions(), tenants=tenants, cognito=Cognito(),
        cursor_signing_key=b"synthetic-catalog-cursor-key-not-production-0123456789",
        catalog_drafts=drafts, read_auditor=auditor, require_persistent_read_audit=True)
    client = TestClient(app)
    headers = {"X-GuardOath-Admin-Session": "synthetic-verified-owner", "Origin": "https://guardoath.com"}
    assert client.get("/v1/admin/catalog").status_code == 401
    assert client.get("/v1/admin/catalog", headers={"X-GuardOath-Key": "customer-key"}).status_code == 401

    response = client.get("/v1/admin/catalog", headers=headers)
    assert response.status_code == 200, response.text
    admin = response.json()
    history = catalog_metadata.load_release_history()
    source = read_catalog(published_catalog)
    assert admin["metadata_releases"] == verified_catalog_history(source, history)
    reader = AdminAuditReader(db, "audit_table", verify_key=ADMIN_BROWSER_AUDIT_KEY)
    assert reader.read(admin["read_audit_event_id"])["integrity_status"] == "signed_verified"
    customer_response = client.get("/v1/controls")
    assert customer_response.status_code == 200
    customer = customer_response.json()
    assert read_catalog(lambda: customer) == source
    assert {k: v for k, v in admin.items() if k not in ("status", "metadata_releases", "read_audit_event_id")} == source

    rule = rule_catalog.RULES["agent.git_force"]
    outcomes = {}
    for label in configs:
        record = store.get("usr-catalog-" + label)
        engine = tenants.engine_for(record)
        for stage in (Stage.INPUT, Stage.OUTPUT):
            for case, content in (("positive", rule.example), ("benign", rule.look_alike)):
                result = asyncio.run(engine.check(CheckRequest(application_id=record.tenant_id,
                    policy_version=TENANT_POLICY_VERSION, content=content), stage))
                detail = {"complete": result.evaluation_status, "action": result.effective_action.value,
                          "findings": sorted(f.rule_id for f in result.findings),
                          "checkers": sorted(c.checker_id for c in result.checker_results)}
                assert detail["complete"] == "complete", detail
                expected_block = case == "positive" and (label == "enabled" or stage == Stage.OUTPUT)
                assert detail["action"] == ("block" if expected_block else "allow"), detail
                assert ("agent.git_force" in detail["findings"]) is expected_block, detail
                assert ("lexical_guards" in detail["checkers"]) is not (label == "parent-off" and stage == Stage.INPUT)
                outcomes[label + ":" + stage.value + ":" + case] = detail

    if args.plan is not None:
        if args.initialize_store and args.next_title == rule.title:
            args.next_title = "Alternate reviewed force-push guidance"
        assert args.next_title and args.next_title != rule.title
        proposal = {"expected_registry_version": source["registry_version"],
            "proposed_registry_version": str(int(source["registry_version"]) + 1),
            "category_id": "agent_safety", "check_id": "lexical_guards", "rule_id": rule.rule_id,
            "title": args.next_title, "use_case": rule.use_case,
            "reason": "Verify real source release and customer Off preservation",
            "idempotency_key": "catalog-runtime-draft-" + source["registry_version"]}
        saved = client.post("/v1/admin/catalog/drafts", headers=headers, json=proposal)
        assert saved.status_code == 200, saved.text
        draft = saved.json()
        reviewed = client.post("/v1/admin/catalog/drafts/" + draft["draft_id"] + "/review",
            headers=headers, json={"confirmation": "REVIEW " + draft["draft_id"][-8:],
            "reason": "Verify full source metadata and inverse before publication",
            "idempotency_key": "catalog-runtime-review-" + source["registry_version"]})
        assert reviewed.status_code == 200, reviewed.text
        review = reviewed.json()
        manifest_response = client.get("/v1/admin/catalog/reviews/" + review["review_id"] + "/manifest", headers=headers)
        assert manifest_response.status_code == 200, manifest_response.text
        manifest = manifest_response.json()
        reopened = AdminTestDynamo(args.database)
        reopened_drafts = AdminCatalogDrafts(reopened, "audit_table", ADMIN_BROWSER_AUDIT_KEY)
        saved_draft = reopened_drafts.read(owner, draft["draft_id"])
        saved_review = reopened_drafts.read_review(owner, review["review_id"])
        assert saved_draft["proposal_sha256"] == saved_review["proposal_sha256"] == manifest["reviewed_proposal_sha256"]
        assert saved_draft["baseline_sha256"] == saved_review["baseline_sha256"] == manifest["reviewed_source_sha256"]
        reopened.close()
        assert manifest["published"] is False and manifest["acceptance_tests_run"] is False
        assert manifest["reversible_patch"]["round_trip_verified"] is True
        assert manifest["tenant_policies_changed"] is False and manifest["detector_behavior_changed"] is False
        # This is the existing browser export contract, not a test-only plan.
        patch = manifest["reversible_patch"]
        plan = {"format": "guardoath.reviewed-catalog-metadata-source-plan.v1",
                "status": "review_only_not_published",
                **{k: manifest[k] for k in ("review_id", "draft_id", "registry_version",
                    "candidate_registry_version", "reviewed_source_sha256", "reviewed_proposal_sha256",
                    "candidate_metadata_sha256", "rollback_source_sha256", "changed_fields", "changed_metadata")},
                "target": deepcopy(patch["target"]), "forward": deepcopy(patch["forward"]),
                "rollback": deepcopy(patch["rollback"]), "round_trip_verified": True,
                "hashes_are_not_signatures": True, "executable_source_committed": False,
                "published": False, "tenant_policies_changed": False,
                "detector_behavior_changed": False, "acceptance_tests_run": False}
        args.plan.write_text(json.dumps(plan), encoding="utf-8")

    assert tenant_rows() == before
    report = {"registry_version": source["registry_version"], "admin": source, "customer": customer,
              "history": history, "tenant_rows": before, "outcomes": outcomes,
              "rules": {key: asdict(value) for key, value in rule_catalog.RULES.items()}}
    args.report.write_text(json.dumps(report), encoding="utf-8")
    db.close()
    print(json.dumps({"version": source["registry_version"], "rules": len(rule_catalog.RULES),
                      "engine_cases": len(outcomes), "tenant_records_unchanged": True}))


if __name__ == "__main__":
    main()
