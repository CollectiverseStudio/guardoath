"""Offline owner browser acceptance with the actual mounted application.

Real browser/API routing, signed Cognito JWT validation, fresh owner/MFA
authorization, tenant adapter, transactions, and audit readers are exercised.
Only external Cognito, Stripe and DynamoDB boundaries are synthetic. This app
is not imported by GuardOath and has no provider credentials or cloud clients.
"""
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.parse import parse_qs

# Importing guardoath.app constructs its default app. Pin that import to local
# configuration too, even when a developer launches this test module directly.
for name, value in {"GUARDOATH_ADMIN_ENABLED":"0","GUARDOATH_INVOCATIONS_ENABLED":"0",
        "GUARDOATH_TENANT_STORE":"memory","GUARDOATH_DEMO_KEY_SECRET":"synthetic-admin-browser-demo-key-not-production-0123456789",
        "GUARDOATH_COGNITO_POOL_ID":"","GUARDOATH_COGNITO_CLIENT_ID":"","GUARDOATH_COGNITO_DOMAIN":"",
        "GUARDOATH_COGNITO_REDIRECT_URI":"","GUARDOATH_SELF_REGISTRATION":"0","GUARDOATH_GOOGLE_LOGIN_ENABLED":"0",
        "GUARDOATH_PACK_FILE":"","GUARDOATH_POLICY_FILE":"","AWS_EC2_METADATA_DISABLED":"true"}.items():
    os.environ[name] = value

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY
from guardoath.admin_actions import AdminTenantActions
from guardoath.admin_audit import AdminAuditReader, AdminSensitiveReadAuditor
from guardoath.admin_billing import AdminSubscriptionReporter
from guardoath.admin_billing_reconcile import AdminBillingDowngrade
from guardoath.admin_browser import BrowserAuthConfig, BoundExchange
from guardoath.admin_catalog_drafts import AdminCatalogDrafts
from guardoath.admin_catalog import published_catalog
from guardoath.admin_catalog_publication import AdminCatalogPublications
from guardoath.admin_catalog_publication_state import DynamoCatalogPublicationState
from guardoath.catalog_metadata import load_release_history
from guardoath.admin_integration import mount_admin_console
from guardoath.admin_invoices import AdminInvoiceHistory
from guardoath.admin_subscription_reconcile import AdminSubscriptionReconciliation
from guardoath.app import create_app, demo_engine
from guardoath.checkers import default_checkers
from guardoath.dynamodb_store import DynamoDBTenantStore, to_item
from guardoath.hosted import TenantService
from guardoath.rule_catalog import RULES
from guardoath.subscription_reconciliation import StripeBinding
from guardoath.tenant_record import TenantRecord
from guardoath.tenant_sessions import CognitoSessions
from guardoath import state_contracts

# Production has no environment phase switch. The source phase preserves every
# source-controlled gate. The compatibility fixture explicitly disables all
# writers; the retained financial writer fixture changes only financial gates.
phase = os.environ.get("GUARDOATH_ADMIN_TEST_WRITER_PHASE", "source")
if phase not in ("0", "1", "source"):
    raise RuntimeError("Invalid synthetic browser phase")
if phase == "0":
    state_contracts.ENABLE_NEW_SUBSCRIPTION_STATE = False
    state_contracts.ENABLE_NEW_CANCELLATION_STATE = False
    state_contracts.ENABLE_NEW_CATALOG_PUBLICATION_STATE = False
    state_contracts.ENABLE_NEW_CATALOG_PUBLICATION_LIFECYCLE_STATE = False
elif not (state_contracts.new_catalog_publication_state_enabled()
          and state_contracts.new_catalog_publication_lifecycle_state_enabled()):
    raise RuntimeError("Catalog acceptance requires activated source defaults")
if phase == "1":
    state_contracts.ENABLE_NEW_SUBSCRIPTION_STATE = True
    state_contracts.ENABLE_NEW_CANCELLATION_STATE = True
if phase == "source" and not (state_contracts.new_subscription_state_enabled()
        and state_contracts.new_cancellation_state_enabled()):
    raise RuntimeError("Financial acceptance requires activated source defaults")

POOL, CLIENT = "us-east-1_Example", "syntheticownerclient"
DOMAIN = "operators.auth.us-east-1.amazoncognito.com"
ORIGIN = os.environ.get("GUARDOATH_ADMIN_TEST_ORIGIN", "http://127.0.0.1:8780")
if ORIGIN not in ("http://127.0.0.1:8780", "http://127.0.0.1:8781"):
    raise RuntimeError("Browser acceptance must stay on its isolated loopback origin")
OWNER = "12345678-1234-1234-1234-123456789012"
CUSTOMER = "87654321-4321-4321-4321-210987654321"
CURSOR_KEY = b"synthetic-owner-cursor-key-not-production-0123456789"
_rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def integer(value):
    return b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


public = _rsa.public_key().public_numbers()
JWKS = {"keys":[{"kid":"synthetic-browser","kty":"RSA","alg":"RS256","use":"sig",
                 "n":integer(public.n),"e":integer(public.e)}]}


def signed_token(code):
    now = int(time.time())
    claims = {"token_use":"access","client_id":CLIENT,
        "iss":f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}","exp":now + 3600,
        "scope":"openid email aws.cognito.signin.user.admin",
        "sub":CUSTOMER if code == "customer" else OWNER,
        "username":"customer" if code == "customer" else "operator",
        "amr":["pwd"] if code == "no-mfa" else ["pwd","mfa"],
        "auth_time":now - 1000 if code == "stale-mfa" else now}
    payload = b64(json.dumps({"alg":"RS256","kid":"synthetic-browser"}).encode()) + "." + b64(json.dumps(claims).encode())
    signature = _rsa.sign(payload.encode(),padding.PKCS1v15(),hashes.SHA256())
    if code == "forged": signature = bytes([signature[0] ^ 1]) + signature[1:]
    return payload + "." + b64(signature)


def jwks_get(url):
    assert url == f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}/.well-known/jwks.json"
    return 200, json.dumps(JWKS).encode()


def unexpected_post(*_args, **_kwargs):
    raise AssertionError("Unexpected identity network request")


sessions = CognitoSessions(pool_id=POOL,client_id=CLIENT,domain=DOMAIN,
    redirect_uri=ORIGIN + "/dashboard",get=jwks_get,post=unexpected_post)


class CognitoBoundary:
    def __init__(self):
        self.customer_enabled = True

    def get_user(self, *, AccessToken):
        claims = sessions.verified_claims(AccessToken)
        assert claims is not None
        return {"Username":claims["username"]}

    def admin_get_user(self, *, UserPoolId, Username):
        assert UserPoolId == POOL and Username in ("operator","customer")
        owner = Username == "operator"
        return {"Username":Username,"Enabled":True if owner else self.customer_enabled,"UserStatus":"CONFIRMED",
            "UserAttributes":[{"Name":"sub","Value":OWNER if owner else CUSTOMER},
                {"Name":"email","Value":"operator@example.invalid" if owner else "customer@example.invalid"},
                {"Name":"email_verified","Value":"true"}]}

    def admin_list_groups_for_user(self, *, UserPoolId, Username, Limit):
        assert UserPoolId == POOL and Limit == 60
        return {"Groups":[{"GroupName":"GuardOathOwners"}] if Username == "operator" else []}

    def list_users(self, *, UserPoolId, Limit, Filter=None):
        assert UserPoolId == POOL and 1 <= Limit <= 60
        row = self.admin_get_user(UserPoolId=POOL,Username="customer")
        row["Attributes"] = row.pop("UserAttributes")
        rows = [row] if Filter is None or Filter == 'email = "customer@example.invalid"' else []
        return {"Users":rows}

    def admin_disable_user(self, *, UserPoolId, Username):
        assert UserPoolId == POOL and Username == "customer"
        self.customer_enabled = False
        return {}

    def admin_enable_user(self, *, UserPoolId, Username):
        assert UserPoolId == POOL and Username == "customer"
        self.customer_enabled = True
        return {}


config = BrowserAuthConfig(domain=DOMAIN,client_id=CLIENT,redirect_uri=ORIGIN + "/admin/login")


def exchange_code(url, body):
    assert url == "https://" + DOMAIN + "/oauth2/token"
    fields = parse_qs(body.decode())
    assert fields["client_id"] == [CLIENT] and fields["redirect_uri"] == [config.redirect_uri]
    assert fields["grant_type"] == ["authorization_code"] and fields["code_verifier"] == ["v" * 43]
    code = fields["code"][0]
    assert code in ("owner","customer","no-mfa","stale-mfa","forged")
    return {"access_token":signed_token(code),"token_type":"Bearer"}


path = Path(os.environ["GUARDOATH_ADMIN_TEST_DB"])
path.parent.mkdir(parents=True,exist_ok=True)
if path.exists(): raise RuntimeError("Browser acceptance requires a new isolated database")
db = AdminTestDynamo(path)
month = datetime.now(timezone.utc).strftime("%Y-%m")
for suffix in ("alpha","beta"):
    baseline_active = phase == "0" and suffix == "alpha"
    db.seed("tenant_table",to_item(TenantRecord(tenant_id="usr-admin-" + suffix,
        display_name="Synthetic " + suffix, demo=False, status="active", revision=7,
        subscription_status="active" if baseline_active else "inactive",billing_mode="stripe",payer_id="cus_" + suffix,
        stripe_subscription_id="sub_" + suffix if baseline_active else None,
        monthly_allowance=10000 if phase == "0" else 0,overage="block",key_hashes=("a" * 64,),usage={month:10},
        input_check_pii="block",output_check_pii="block",input_check_text_security="block",
        input_rule_actions={"pii.email_address":"off"},input_attachment_csv_mode="off")))
store = DynamoDBTenantStore("tenant_table",db)
checkers = default_checkers(enable_pii=True)
tenants = TenantService(store,checkers)
app = create_app(engine=demo_engine(enable_pii=True,checkers=checkers),credentials={},tenants=tenants)


subscription_reads = []


def stripe_snapshot(tenant_id, customer_id, subscription_id):
    subscription_reads.append((tenant_id, customer_id, subscription_id))
    suffix = tenant_id.removeprefix("usr-admin-")
    assert suffix in ("alpha","beta") and customer_id == "cus_" + suffix and subscription_id == "sub_" + suffix
    if suffix == "alpha":
        return json.loads(json.dumps(cancellation_boundary.value))
    return {"object":"subscription","id":subscription_id,"customer":customer_id,"status":"active",
        "livemode":False,"pause_collection":None,"collection_method":"charge_automatically",
        "items":{"has_more":False,"data":[{"quantity":1,"price":{"id":"price_starter"}}]}}


def resolve_subscription(record):
    binding = StripeBinding(record.tenant_id,record.payer_id,
        record.stripe_subscription_id or "sub_" + record.tenant_id.removeprefix("usr-admin-"))
    return binding, stripe_snapshot(binding.tenant_id,binding.customer_id,binding.subscription_id)


def stripe_invoices(tenant_id, customer_id, subscription_id, *, starting_after=None, status=None):
    """Only external provider transport is synthetic; real cursor/API/audit run above it."""
    suffix = tenant_id.removeprefix("usr-admin-")
    assert suffix in ("alpha", "beta") and customer_id == "cus_" + suffix and subscription_id == "sub_" + suffix
    with path.with_suffix(".invoice-reads.jsonl").open("a", encoding="utf-8") as evidence:
        evidence.write(json.dumps({"tenant_id":tenant_id, "starting_after":starting_after,
                                   "status":status}) + "\n")
    rows = [{"object":"invoice", "id":f"in_{suffix}_{index}", "customer":customer_id,
        "subscription":subscription_id, "livemode":False,
        "status":"open" if index == 6 else "uncollectible" if index == 7 else "paid",
        "created":1795161600 - index * 86400, "currency":"eur" if index == 7 else "usd",
        "amount_due":700 + index, "amount_paid":700 + index if index < 6 else 0,
        "amount_remaining":0 if index < 6 else 700 + index,
        "collection_method":"charge_automatically", "attempted":True, "attempt_count":1,
        "next_payment_attempt":None, "customer_email":"private-invoice@example.invalid",
        "hosted_invoice_url":"https://private-invoice.example.invalid"} for index in range(8)]
    if status is not None:
        rows = [row for row in rows if row["status"] == status]
    if starting_after is not None:
        at = next(i for i, row in enumerate(rows) if row["id"] == starting_after)
        rows = rows[at + 1:]
    return {"object":"list", "data":rows[:5], "has_more":len(rows) > 5}


reader = AdminAuditReader(db,"audit_table",verify_key=ADMIN_BROWSER_AUDIT_KEY)
reporter = AdminSubscriptionReporter(stripe_snapshot,expected_price_id="price_starter",expected_livemode=False)
from admin_cancellation_support import ProviderBoundary
from guardoath.admin_cancellation_provider import StripePeriodEndCancellation
from guardoath.admin_cancellation_state import DynamoCancellationState
from guardoath.admin_subscription_cancellation import AdminSubscriptionCancellation

cancellation_boundary = ProviderBoundary()
cancellation_boundary.value.update(id="sub_alpha", customer="cus_alpha",
    current_period_start=int(time.time())-86400, current_period_end=int(time.time())+86400*10)
cancellation_boundary.value["items"]["data"][0].update(id="si_alpha", subscription="sub_alpha")
cancellation_boundary.value["items"]["data"][0]["price"]["id"] = "price_starter"

def cancellation_evidence(request):
    with path.with_suffix(".cancellation-requests.jsonl").open("a",encoding="utf-8") as evidence:
        evidence.write(json.dumps({"method":request.get_method(), "url":request.full_url,
            "body":request.data.decode() if request.data else None,
            "idempotency_key":request.get_header("Idempotency-key")}) + "\n")

cancellation_boundary.before = cancellation_evidence
cancellation = AdminSubscriptionCancellation(
    state=DynamoCancellationState(db,"audit_table",ADMIN_BROWSER_AUDIT_KEY),
    tenant_table="tenant_table", preview_key=CURSOR_KEY,
    provider=StripePeriodEndCancellation(api_key="rk_test_synthetic_cancel_0123456789",
        expected_account_id="acct_owner",expected_price_id="price_starter",expected_livemode=False,
        opener=cancellation_boundary.open))
catalog_drafts = AdminCatalogDrafts(db,"audit_table",ADMIN_BROWSER_AUDIT_KEY)
catalog_publications = AdminCatalogPublications(
    state=DynamoCatalogPublicationState(db,"audit_table",ADMIN_BROWSER_AUDIT_KEY),
    drafts=catalog_drafts,catalog_provider=published_catalog,history_provider=load_release_history,
    source_provider=lambda: "a" * 40)
mount_admin_console(app,sessions=sessions,tenants=tenants,cognito=CognitoBoundary(),
    cursor_signing_key=CURSOR_KEY,browser_config=config,browser_exchange=BoundExchange(config,sender=exchange_code),
    admin_mutator=AdminTenantActions(db,"tenant_table","audit_table",audit_key=ADMIN_BROWSER_AUDIT_KEY),
    audit_reader=reader,read_auditor=AdminSensitiveReadAuditor(db,"audit_table",signing_key=ADMIN_BROWSER_AUDIT_KEY),
    catalog_drafts=catalog_drafts,catalog_publications=catalog_publications,require_persistent_read_audit=True,
    billing_reporter=reporter,
    billing_cancellation=cancellation,
    billing_configuration={"signed_audit":True, "stripe_read":True,
        "plan_allowance":True, "cancellation_provider":True},
    billing_invoices=AdminInvoiceHistory(stripe_invoices, reporter, cursor_key=CURSOR_KEY),
    billing_downgrade=AdminBillingDowngrade(db,"tenant_table","audit_table",signing_key=ADMIN_BROWSER_AUDIT_KEY,
        reporter=reporter,validate_record=TenantRecord.model_validate),
    billing_reconciler=AdminSubscriptionReconciliation(db,"tenant_table","audit_table",signing_key=ADMIN_BROWSER_AUDIT_KEY,
        resolve_snapshot=resolve_subscription,expected_price_id="price_starter",expected_livemode=False,plan_monthly_allowance=10000),
    record_validator=TenantRecord.model_validate,rule_exists=lambda rule_id:rule_id in RULES)
