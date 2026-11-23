"use strict";

// Explicitly synthetic browser fixtures. They never change the production router,
// Cognito verifier, MFA requirements, tenant records or provider entitlements.
const { execFileSync } = require("node:child_process");
const path = require("node:path");

const TOKEN = "synthetic-admin-browser-token-not-a-credential";
const RECEIPT = "a".repeat(64);
const FIXTURE_TIME = "2026-11-20T12:00:00Z";

function loadAdminSource() {
  const filename = path.resolve(__dirname, "../../guardoath/admin_browser.py");
  const program = [
    "import ast,json,sys",
    "tree=ast.parse(open(sys.argv[1],encoding='utf-8').read())",
    "values={t.id:ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) for t in n.targets if isinstance(t,ast.Name) and t.id in ('_PAGE','_JS')}",
    "print(json.dumps(values))",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON || "python", ["-c", program, filename], {encoding:"utf8"}));
}

function createAdminFixture(options = {}) {
  const calls = [];
  let enrollmentPending = null;
  let enrollmentSequence = 0;
  const source = options.source || loadAdminSource();
  const tenants = [
    {tenant_id:"sample-studio", display_name:"Example Studio (sample)", status:"active", demo:false,
      subscription_status:"active", monthly_allowance:10000, usage_month_count:1460, api_key_count:1, revision:4},
    {tenant_id:"sample-labs", display_name:"Example Labs (sample)", status:"disabled", demo:false,
      subscription_status:"past_due", monthly_allowance:0, usage_month_count:null, api_key_count:0, revision:7},
  ];
  const users = [
    {username:"user-5fb83e61eb6350fe", email_hint:"a***@example.invalid", email_verified:true,
      enabled:true, status:"CONFIRMED", created_utc:FIXTURE_TIME, updated_utc:FIXTURE_TIME},
    {username:"user-b0203bd41425ef99", email_hint:"b***@example.invalid", email_verified:false,
      enabled:false, status:"UNCONFIRMED", created_utc:FIXTURE_TIME, updated_utc:FIXTURE_TIME},
  ];
  const catalog = {registry_version:"3", source:"Synthetic browser catalog sample", read_only:true,
    metadata_releases:{status:"verified_source_history", source:"Synthetic shipped history sample",
      registry_version:"3", release_count:0, has_older_releases:false,
      source_chain_verified:true, live_acceptance_verified:null, releases:[]},
    total_categories:2, total_checks:2, total_rules:2, categories:[
      {category_id:"privacy", name:"Privacy and personal data", status:"active", checks:[
        {check_id:"pii", name:"Personal data", rule_count:1,
         stages:["input","output"], supported_actions:["block","flag","allow","redact"],
         use_case:"Inspect identifiers in prompts and answers",
         limitations:"Free-text names are not reliably detected",
         test_evidence:{unit_test_files_declared:2,evaluation_files_declared:1,
           ci_verified_for_source:false,live_deployment_verified:null},
         rules:[
          {rule_id:"pii.email", title:"Email address", use_case:"Review personal identifiers in prompts and responses."}]}]},
      {category_id:"secrets", name:"Secrets and confidential information", status:"active", checks:[
        {check_id:"secrets", name:"Credentials and secrets", rule_count:1, rules:[
          {rule_id:"secret.synthetic", title:"Synthetic credential example", use_case:"Preview a catalog description without changing a detector."}]}]},
    ]};
  const features = {tenant_status_changes:false, tenant_rule_changes:false, monthly_cost_caps:false,
    tenant_key_revocation:false, cognito_user_status_changes:false, billing_downgrade:false,
    billing_verify:true, billing_invoices:true, catalog_preview:true, catalog_drafts:false,
    catalog_review_receipts:false, audit_receipt_lookup:true, sensitive_read_audit:true,
    ...options.features};
  const json = (data, status = 200) => ({status, contentType:"application/json", body:JSON.stringify(data)});
  const withReceipt = data => ({...data, read_audit_event_id:RECEIPT});
  const fixture = {calls, tenants, users, catalog, features, source, token:TOKEN, receipt:RECEIPT,
    override:options.override || null,
    resolve(rawUrl, method = "GET", rawHeaders = {}, rawBody = null) {
      const url = new URL(rawUrl, "http://127.0.0.1:8777");
      const pathname = url.pathname;
      const headers = Object.fromEntries(Object.entries(rawHeaders).map(([k,v]) => [k.toLowerCase(),v]));
      let body = rawBody;
      if (typeof rawBody === "string") {try {body = JSON.parse(rawBody);} catch {body = null;}}
      const request = {url, path:pathname, method, headers, body};
      calls.push(request);
      if (typeof fixture.override === "function") {
        const result = fixture.override(request, fixture);
        if (result) return result;
      }
      if (pathname === "/admin/login" || pathname === "/admin")
        return {status:200, contentType:"text/html", body:source._PAGE};
      if (pathname === "/admin/client.js")
        return {status:200, contentType:"text/javascript", body:source._JS};
      if (pathname === "/v1/admin/auth/config") return json({
        domain:"example.auth.us-east-1.amazoncognito.com", client_id:"fixtureclient123",
        redirect_uri:"http://127.0.0.1:8777/admin/login", native_mfa_enabled:options.nativeMfa === true,
        native_enrollment_enabled:options.nativeMfa === true && options.nativeEnrollment === true});
      if (pathname === "/v1/admin/auth/exchange" && method === "POST") {
        return body?.code === "fixture-code" && typeof body?.code_verifier === "string"
          ? json({access_token:TOKEN,token_type:"Bearer"}) : json({detail:"Fixture exchange denied"},401);
      }
      if (pathname === "/v1/admin/auth/native/start" && method === "POST" && options.nativeMfa === true)
        return json({challenge:"software_token_mfa", challenge_ticket:"synthetic-mfa-ticket-".repeat(4)});
      if (pathname === "/v1/admin/auth/native/finish" && method === "POST" && options.nativeMfa === true)
        return body?.otp === "123456" ? json({access_token:TOKEN,token_type:"Bearer"}) : json({detail:"Fixture OTP denied"},401);
      // Only synthetic state for exercising the actual UI. Security/CAS/token
      // behavior is independently tested against the Python implementation.
      if (options.nativeMfa === true && options.nativeEnrollment === true && method === "POST") {
        if (pathname === "/v1/admin/auth/enrollment/start") {
          enrollmentPending = "e1.synthetic-enrollment-ticket-" + (++enrollmentSequence);
          return json({setup_key:"A".repeat(32), enrollment_ticket:enrollmentPending, expires_in:180});
        }
        if (pathname === "/v1/admin/auth/enrollment/cancel") {
          if (!enrollmentPending || body?.ticket !== enrollmentPending) return json({detail:"Fixture setup denied"},401);
          enrollmentPending = null;
          return json({status:"cancelled"});
        }
        if (pathname === "/v1/admin/auth/enrollment/finish") {
          if (!enrollmentPending || body?.ticket !== enrollmentPending) return json({detail:"Fixture setup denied"},401);
          if (body?.code !== "123456") return json({detail:"Fixture code mismatch",code:"code_mismatch"},400);
          enrollmentPending = null;
          return json({status:"completed",sign_in_required:true});
        }
      }
      if (!pathname.startsWith("/v1/admin/")) return json({detail:"No fixture route"},404);
      if (headers["x-guardoath-admin-session"] !== TOKEN) return json({detail:"Fixture owner session required"},401);
      if (pathname === "/v1/admin/overview") return json(withReceipt({status:"read_only", metrics:{
        users:{value:null,source:"Cognito totals unavailable"},
        subscriptions:{value:null,source:"Subscription rollup unavailable"},
        revenue:{value:null,source:"Verified revenue source unavailable"},
        aws_costs:{value:null,source:"AWS cost source unavailable"}}}));
      if (pathname === "/v1/admin/features") return json(withReceipt(features));
      if (pathname === "/v1/admin/tenants") {
        const filtered = tenants.filter(t => t.tenant_id.includes(url.searchParams.get("search") || ""));
        return json(withReceipt({status:"ok",tenants:filtered,source:"Synthetic tenant fixtures",
          next_cursor:null,total:null,returned:filtered.length,scanned:filtered.length,
          retrieved_utc:FIXTURE_TIME}));
      }
      if (pathname === "/v1/admin/users") {
        let selected = users;
        const query = url.searchParams.get("query") || "";
        if (query) selected = selected.filter(u => u.email_hint.startsWith(query));
        if (url.searchParams.has("cursor")) selected = [users[1]];
        return json(withReceipt({status:"ok",users:selected,source:"Synthetic Cognito page",
          retrieved_utc:FIXTURE_TIME,returned:selected.length,total:null,
          next_cursor:url.searchParams.has("cursor") || query ? null : "fixture-user-next"}));
      }
      if (pathname === "/v1/admin/users/lookup" && method === "POST") return json(withReceipt({
        status:"ok",user:users[0],source:"Synthetic Cognito exact lookup"}));
      if (pathname === "/v1/admin/catalog") return json(withReceipt(catalog));
      if (/^\/v1\/admin\/tenants\/[^/]+\/usage$/.test(pathname)) return json(withReceipt({usage:{
        tenant_id:pathname.split("/")[4],period_utc:"2026-11", requests_recorded:1460, monthly_allowance:10000,
        remaining_before_allowance:8540, overage_policy:"block", record_updated_at:FIXTURE_TIME,
        usage_threshold:{state:"within_limit",utilization_percent_floor:14,policy:"blocks_at_saved_cap"},
        quota_pacing:{state:"projected_within_limit",projected_month_end_requests_floor:2190,
          record_metadata_freshness:"recent_record_metadata",calendar_days_elapsed:20,
          calendar_days_in_month:30,persisted_alert:false},
        historical_volume:{state:"within_historical_range",history_months_utc:["2026-10","2026-09","2026-08"],
          history_median_monthly_requests:2300,projected_vs_history_percent_floor:95,
          persisted_alert:false},
        source:"Synthetic saved tenant record"}}));
      if (/^\/v1\/admin\/subscriptions\/[^/]+\/invoices$/.test(pathname)) return json(withReceipt({tenant_id:pathname.split("/")[4],history:{
        verified:true,status:"verified",count:1,has_more:false,read_at_utc:FIXTURE_TIME,
        invoices:[{status:"paid",currency:"usd",created_utc:FIXTURE_TIME,
          amount_due_minor:2900,amount_paid_minor:2900,amount_remaining_minor:0,
          collection_method:"charge_automatically",attempted:true,attempt_count:1,
          next_payment_attempt_utc:null}]}}));
      if (/^\/v1\/admin\/subscriptions\/[^/]+$/.test(pathname)) return json(withReceipt({tenant_id:pathname.split("/")[4],subscription:{
        verified:true,status:"active",verified_at:FIXTURE_TIME,current_period_end_utc:"2026-12-20T12:00:00Z",
        cancel_at_period_end:false,reconciliation:{level:"consistent",code:"agreement",
          next_step:"Synthetic saved entitlement agrees with sample provider state"}}}));
      if (/^\/v1\/admin\/audit\/[a-f0-9]{64}$/.test(pathname)) return json(withReceipt({audit:{
        event_id:pathname.split("/").pop(),status:"ok",integrity_status:"signed_verified",operation:"admin.read",
        change:{field:"protected_read",resource:"tenant.directory"},tenant_id:"sample-studio",when_utc:FIXTURE_TIME}}));
      if (pathname === "/v1/admin/catalog/preview" && method === "POST") return json(withReceipt({
        status:"preview_only",published:false,persisted:false,registry_version:body.expected_registry_version,
        rule_id:body.rule_id,tenant_policies_changed:false,detector_behavior_changed:false,
        changed_fields:["title","use_case"],fingerprint_kind:"review_only_untrusted_sha256_not_signature",
        baseline_sha256:"b".repeat(64),proposal_sha256:"c".repeat(64),candidate_registry_version:"4",
        requires:"Synthetic preview only; no production changes"}));
      return json({detail:"Fixture does not permit this operation"},403);
    },
  };
  return fixture;
}

async function installAdminFixture(page, options = {}) {
  const fixture = createAdminFixture(options);
  await page.route("**/*", async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:8777") return route.abort();
    const response = fixture.resolve(url.href, request.method(), request.headers(), request.postData());
    return route.fulfill(response);
  });
  return fixture;
}

async function openFixtureSession(page) {
  await page.addInitScript(() => sessionStorage.setItem("go_admin_pkce",
    JSON.stringify({state:"fixture-state",verifier:"v".repeat(43)})));
  await page.goto("http://127.0.0.1:8777/admin/login?code=fixture-code&state=fixture-state");
}

module.exports = {loadAdminSource, createAdminFixture, installAdminFixture, openFixtureSession};
