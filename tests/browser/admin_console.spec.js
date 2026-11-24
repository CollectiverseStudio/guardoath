"use strict";

const {test, expect} = require("@playwright/test");
const {installAdminFixture, openFixtureSession} = require("./admin_fixture");

// These execute the real admin HTML and JS. Synthetic API responses deliberately
// isolate browser behavior; this is not evidence of live AWS or MFA acceptance.
const sections = ["overview","users","tenants","billing","usage","audit","catalog"];
async function signedIn(page, options) {
  const fixture = await installAdminFixture(page, options);
  await openFixtureSession(page);
  await expect(page.locator("#app")).toBeVisible();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  return fixture;
}

async function selectCatalogDescription(page) {
  await page.locator("#nav-catalog").click();
  // The rule action becomes visible only after the real category/check disclosures.
  const category = page.locator("#categories > details").first();
  await category.locator(":scope > summary").click();
  const check = category.locator(":scope > details").first();
  await check.locator(":scope > summary").click();
  await check.getByRole("button", {name:"Preview description", exact:true}).click();
  await expect(page.locator("#proposal-go")).toBeEnabled();
}

test("owner workspace navigates every section and retains honest unavailable totals", async ({page}) => {
  const fixture = await signedIn(page);
  await expect(page.locator("#metrics .metric strong")).toHaveText(["Unavailable","Unavailable","Unavailable","Unavailable","2","1","Unavailable","Unavailable","Unavailable"]);
  for (const section of sections) {
    await page.locator("#nav-" + section).click();
    await expect(page.locator("#section-" + section)).toBeVisible();
    await expect(page.locator("#nav-" + section)).toHaveAttribute("aria-current","page");
    await expect(page.locator("[data-admin-section]:visible")).toHaveCount(1);
  }
  const privateCalls = fixture.calls.filter(c => c.path.startsWith("/v1/admin/") && !c.path.startsWith("/v1/admin/auth/"));
  expect(privateCalls.length).toBeGreaterThanOrEqual(5);
  for (const call of privateCalls) {
    expect(call.headers["x-guardoath-admin-session"]).toBe(fixture.token);
    expect(call.headers["x-guardoath-key"]).toBeUndefined();
    expect(call.headers.authorization).toBeUndefined();
  }
  expect(await page.evaluate(() => Object.values(sessionStorage).join(" "))).not.toContain(fixture.token);
  expect(await page.evaluate(() => Object.values(localStorage).join(" "))).not.toContain(fixture.token);
});

test("failed core owner refresh withdraws old views and recovers without inventing totals", async ({page}) => {
  const fixture = await signedIn(page);
  await expect(page.locator("#metrics .metric")).toHaveCount(9);
  await expect(page.locator("#tenant-rows tr")).toHaveCount(2);
  await expect(page.locator("#categories details")).not.toHaveCount(0);
  fixture.override = request => request.path === "/v1/admin/catalog"
    ? {status:503,contentType:"application/json",body:'{"detail":"provider unavailable"}'} : null;
  await page.locator("#refresh").click();
  await expect(page.locator("#status")).toContainText("Request failed (503)");
  await expect(page.locator("#metrics .metric")).toHaveCount(0);
  await expect(page.locator("#tenant-rows tr")).toHaveCount(0);
  await expect(page.locator("#user-rows tr")).toHaveCount(0);
  await expect(page.locator("#categories details")).toHaveCount(0);
  await expect(page.locator("#catalog-release-summary")).toContainText("Unavailable");
  await expect(page.locator("#proposal-go")).toBeDisabled();
  await expect(page.locator("#user-change")).toBeDisabled();
  await expect(page.locator("#next")).toBeDisabled();
  fixture.override = null;
  await page.locator("#refresh").click();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await expect(page.locator("#metrics .metric")).toHaveCount(9);
  await expect(page.locator("#tenant-rows tr")).toHaveCount(2);
  await expect(page.locator("#categories details")).not.toHaveCount(0);
});

test("older failed workspace refresh cannot erase a newer verified snapshot", async ({page}) => {
  await signedIn(page);
  let releaseOlder, startedOlder, deliveredOlder;
  const held = new Promise(resolve => releaseOlder=resolve);
  const started = new Promise(resolve => startedOlder=resolve);
  const delivered = new Promise(resolve => deliveredOlder=resolve);
  let heldOnce = false;
  await page.route("**/v1/admin/catalog", async route => {
    if (!heldOnce) {
      heldOnce = true;
      startedOlder();
      await held;
      await route.fulfill({status:503,contentType:"application/json",
        body:'{"detail":"old catalog error"}'});
      deliveredOlder();
    } else await route.fallback();
  });
  await page.locator("#refresh").click();
  await started;
  await page.locator("#refresh").click();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await expect(page.locator("#metrics .metric")).toHaveCount(9);
  releaseOlder();
  await delivered;
  await page.waitForTimeout(50);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await expect(page.locator("#metrics .metric")).toHaveCount(9);
  await expect(page.locator("#tenant-rows tr")).toHaveCount(2);
  await expect(page.locator("#catalog-summary")).toContainText("Published registry");
});

test("owner overview counts complete authenticated pages and withdraws totals on stale or partial reads", async ({page}) => {
  const fixture = await signedIn(page,{override:request => request.path === "/v1/admin/users"
    ? {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",source:"Cognito ListUsers",users:[{username:"a"},{username:"b"}],
      returned:2,next_cursor:null,retrieved_utc:"2026-11-22T12:00:00Z"})} : null});
  await expect(page.locator("#metrics .metric strong")).toHaveText(
    ["2","Unavailable","Unavailable","Unavailable","2","1","Unavailable","Unavailable","Unavailable"]);
  await expect(page.locator("#metrics")).toContainText("Cognito ListUsers · complete bounded page");
  await expect(page.locator("#metrics")).toContainText("DynamoDB tenant table · complete bounded page (includes demos)");
  await expect(page.locator("#metrics")).toContainText("NOT Stripe-verified paid subscribers or revenue");
  await expect(page.locator("#metrics")).toContainText("read at 2026-11-22T12:00:00Z");
  // A complete scan with unknown legacy status must never manufacture a
  // saved active total; tenant-record count is still independently valid.
  fixture.override = request => request.path === "/v1/admin/tenants"
    ? {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",tenants:[
        {tenant_id:"sample-studio",display_name:"Studio",status:"active",demo:false,
          subscription_status:"active",revision:4,monthly_allowance:10000},
        {tenant_id:"sample-labs",display_name:"Labs",status:"active",demo:false,
          subscription_status:null,revision:6,monthly_allowance:0}],
      returned:2,scanned:2,next_cursor:null,
      retrieved_utc:"2026-11-22T12:01:00Z"})} : null;
  await page.locator("#refresh").click();
  await expect(page.locator("#metrics .metric strong")).toHaveText(
    ["Unavailable","Unavailable","Unavailable","Unavailable","2","Unavailable","Unavailable","Unavailable","Unavailable"]);
  // A continuation is a lower bound, not a pool total; a Dynamo page
  // missing scanned entries is not a complete tenant population.
  fixture.override = request => request.path === "/v1/admin/users"
    ? {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",users:[{username:"a"}],returned:1,next_cursor:"more"})}
    : request.path === "/v1/admin/tenants"
    ? {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",tenants:[{tenant_id:"sample-studio"}],returned:1,
      scanned:2,next_cursor:null})} : null;
  await page.locator("#refresh").click();
  await expect(page.locator("#metrics .metric strong")).toHaveText(
    ["Unavailable","Unavailable","Unavailable","Unavailable","Unavailable","Unavailable","Unavailable","Unavailable","Unavailable"]);
  // Unavailable Cognito and a not-yet-exhausted empty Dynamo page must
  // never appear as zero or retain an earlier observed value.
  fixture.override = request => request.path === "/v1/admin/users"
    ? {status:503,contentType:"application/json",body:JSON.stringify({detail:"Provider unavailable"})}
    : request.path === "/v1/admin/tenants"
    ? {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",tenants:[],returned:0,scanned:0,next_cursor:"more"})} : null;
  await page.locator("#refresh").click();
  await expect(page.locator("#metrics .metric strong")).toHaveText(
    ["Unavailable","Unavailable","Unavailable","Unavailable","Unavailable","Unavailable","Unavailable","Unavailable","Unavailable"]);
});

test("owner Overview totals recorded UTC-month validation counts only from complete unambiguous pages", async ({page}) => {
  let rows = [
    {tenant_id:"sample-studio",display_name:"Studio",status:"active",demo:false,
      subscription_status:"active",revision:4,monthly_allowance:10000,
      usage_month:"2026-11",usage_month_count:1460},
    {tenant_id:"sample-labs",display_name:"Labs",status:"disabled",demo:false,
      subscription_status:"past_due",revision:7,monthly_allowance:0,
      usage_month:"2026-11",usage_month_count:37}
  ];
  let cursor = null;
  await signedIn(page, {override: request => request.path === "/v1/admin/tenants"
    ? {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",tenants:rows,returned:rows.length,scanned:rows.length,
      next_cursor:cursor,retrieved_utc:"2026-11-22T12:00:00Z"})} : null});
  const total = page.locator("#metrics .metric").filter({hasText:"recorded month to date requests"});
  await expect(total.locator("strong")).toHaveText("1497");
  await expect(total).toContainText("UTC 2026-11");
  await expect(total).toContainText("includes demos");
  await expect(total).toContainText("NOT live traffic, billable usage, AWS cost or Stripe revenue");
  // Missing legacy counter is unknown, not a zero.
  rows = [{...rows[0]},{...rows[1],usage_month_count:null}];
  await page.locator("#refresh").click();
  await expect(total.locator("strong")).toHaveText("Unavailable");
  // Never mix two UTC months even if both counters are present.
  rows = [{...rows[0]},{...rows[1],usage_month:"2026-10",usage_month_count:37}];
  await page.locator("#refresh").click();
  await expect(total.locator("strong")).toHaveText("Unavailable");
  // Identity duplication is an ambiguous population, not a request total.
  rows = [{...rows[0]},{...rows[0]}];
  await page.locator("#refresh").click();
  await expect(total.locator("strong")).toHaveText("Unavailable");
  // A continuation is never a full population.
  rows = [{...rows[0]},{...rows[1],usage_month_count:37}];
  cursor = "remaining";
  await page.locator("#refresh").click();
  await expect(total.locator("strong")).toHaveText("Unavailable");
  // Avoid loss of numerical precision across individually valid records.
  cursor = null;
  rows = [{...rows[0],usage_month_count:Number.MAX_SAFE_INTEGER},{...rows[1],usage_month_count:37}];
  await page.locator("#refresh").click();
  await expect(total.locator("strong")).toHaveText("Unavailable");
});

test("owner Overview saved allowance risk counts only verified full UTC-month tenant snapshots", async ({page}) => {
  let rows = [
    {tenant_id:"sample-studio",display_name:"Studio",status:"active",demo:false,
      subscription_status:"active",revision:4,monthly_allowance:100,
      usage_month:"2026-11",usage_month_count:100},
    {tenant_id:"sample-labs",display_name:"Labs",status:"disabled",demo:false,
      subscription_status:"past_due",revision:7,monthly_allowance:0,
      usage_month:"2026-11",usage_month_count:0},
    {tenant_id:"demo",display_name:"Demo",status:"active",demo:true,
      subscription_status:"inactive",revision:1,monthly_allowance:0,
      usage_month:"2026-11",usage_month_count:10}
  ];
  let cursor = null;
  const fixture = await signedIn(page, {override: request => request.path === "/v1/admin/tenants"
    ? {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",tenants:rows,returned:rows.length,scanned:rows.length,
      next_cursor:cursor,retrieved_utc:"2026-11-22T12:00:00Z"})} : null});
  const cap = page.locator("#metrics .metric").filter({hasText:"saved allowance reached tenants"});
  await expect(cap.locator("strong")).toHaveText("2");
  await expect(cap).toContainText("non-demo accounts");
  await expect(cap).toContainText("NOT AWS cost, active alerts or proof of blocking overages");
  // An active account under its saved cap is not counted; demo accounts never count.
  rows = [{...rows[0],usage_month_count:99}, {...rows[1]}, {...rows[2]}];
  await page.locator("#refresh").click();
  await expect(cap.locator("strong")).toHaveText("1");
  // A legacy/malformed cap is not silently interpreted as zero.
  for (const invalidCap of [null, -1, true, Number.MAX_SAFE_INTEGER]) {
    rows = [{...rows[0],monthly_allowance:invalidCap}, {...rows[1]}, {...rows[2]}];
    await page.locator("#refresh").click();
    await expect(cap.locator("strong")).toHaveText("Unavailable");
  }
  rows = [{...rows[0],monthly_allowance:100}, {...rows[1]}, {...rows[2]}];
  rows[1].usage_month = "2026-10";
  await page.locator("#refresh").click();
  await expect(cap.locator("strong")).toHaveText("Unavailable");
  rows[1].usage_month = "2026-11";
  cursor = "more";
  await page.locator("#refresh").click();
  await expect(cap.locator("strong")).toHaveText("Unavailable");
  cursor = null;
  // Zero needs a truly exhaustive, empty authenticated scan and a UTC read.
  rows = [];
  await page.locator("#refresh").click();
  await expect(cap.locator("strong")).toHaveText("0");
  await page.locator("#logout").click();
  await expect(page.locator("#metrics .metric")).toHaveCount(0);
  expect(fixture.calls.filter(c => c.path === "/v1/admin/tenants").length).toBeGreaterThanOrEqual(7);
});


test("owner saved soft-cap exposure stays unknown until all non-demo policies are verified", async ({page}) => {
  let cursor = null;
  let rows = [
    {tenant_id:"sample-studio",display_name:"Studio",status:"active",demo:false,
      subscription_status:"active",revision:4,monthly_allowance:100,
      overage_policy:"allow_and_record",usage_month:"2026-11",usage_month_count:100},
    {tenant_id:"sample-labs",display_name:"Labs",status:"disabled",demo:false,
      subscription_status:"past_due",revision:7,monthly_allowance:0,
      overage_policy:"block",usage_month:"2026-11",usage_month_count:0},
    {tenant_id:"demo",display_name:"Demo",status:"active",demo:true,
      subscription_status:"inactive",revision:1,monthly_allowance:0,
      overage_policy:null,usage_month:"2026-11",usage_month_count:10}
  ];
  const fixture = await signedIn(page,{override:request =>
    request.path === "/v1/admin/tenants"
    ? {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",tenants:rows,returned:rows.length,scanned:rows.length,
      next_cursor:cursor,retrieved_utc:"2026-11-22T12:00:00Z"})} : null});
  const risk = page.locator("#metrics .metric").filter({hasText:"saved soft cap reached tenants"});
  await expect(risk.locator("strong")).toHaveText("1");
  await expect(risk).toContainText("NOT hard blocking, billed overages, live activity or alerts");
  await page.locator("#nav-tenants").click();
  await expect(page.locator("#tenant-rows tr").filter({hasText:"sample-studio"}))
    .toContainText("Allow and record overage");
  await expect(page.locator("#tenant-rows tr").filter({hasText:"sample-labs"}))
    .toContainText("Block at saved cap");
  rows = [{...rows[0],overage_policy:null},rows[1],rows[2]];
  await page.locator("#refresh").click();
  await expect(risk.locator("strong")).toHaveText("Unavailable");
  await expect(page.locator("#tenant-rows tr").filter({hasText:"sample-studio"}))
    .toContainText("Unavailable");
  rows = [{...rows[0],overage_policy:"allow_and_record",usage_month_count:99},rows[1],rows[2]];
  await page.locator("#refresh").click();
  await expect(risk.locator("strong")).toHaveText("0");
  rows = [{...rows[0],usage_month_count:100},rows[1],rows[2]];
  cursor = "more";
  await page.locator("#refresh").click();
  await expect(risk.locator("strong")).toHaveText("Unavailable");
  cursor = null;
  rows = [];
  await page.locator("#refresh").click();
  await expect(risk.locator("strong")).toHaveText("0");
  await page.locator("#logout").click();
  await expect(page.locator("#metrics .metric")).toHaveCount(0);
  expect(fixture.calls.filter(c => c.path === "/v1/admin/tenants").length).toBeGreaterThanOrEqual(5);
});

test("owner catalog shows safe source evidence counts and eligibility without deployment claims", async ({page}) => {
  await signedIn(page);
  await page.locator("#nav-catalog").click();
  const catalog = page.locator("#categories");
  await expect(catalog).toContainText("Stages: input, output");
  await expect(catalog).toContainText("Actions: block, flag, allow, redact");
  await expect(catalog).toContainText("Declared unit-test files: 2");
  await expect(catalog).toContainText("Declared evaluation files: 1");
  await expect(catalog).toContainText("NOT current CI or live deployment evidence");
  await expect(catalog).toContainText("Check use case: Inspect identifiers in prompts and answers");
  await expect(catalog).toContainText("Limitations: Free-text names are not reliably detected");
  expect(await catalog.innerHTML()).not.toContain("tests/private-one.py");
});

test("tenant actions reveal usage, subscription and invoice results immediately", async ({page}) => {
  await signedIn(page);
  const row = page.locator("#tenant-rows tr").filter({hasText:"sample-studio"});
  for (const [action,section,result,text] of [
    ["Usage","usage","usage-details","Recorded: 1460"],
    ["Verify Stripe","billing","billing-details","Provider: active"],
    ["Invoices","billing","invoice-details","paid USD due 2900"],
  ]) {
    await page.locator("#nav-tenants").click();
    await row.getByRole("button",{name:action,exact:true}).click();
    await expect(page.locator("#section-"+section)).toBeVisible();
    await expect(page.locator("#nav-"+section)).toHaveAttribute("aria-current","page");
    await expect(page.locator("#"+result)).toContainText(text);
    if (action === "Usage") {
      await expect(page.locator("#usage-details")).toContainText("Projected recorded month-end requests: 2190");
      await expect(page.locator("#usage-details")).toContainText("Advisory linear pacing only");
    }
    await expect(page.locator("#section-"+section)).toBeFocused();
  }
});

test("later tenant Usage selection wins over a delayed earlier successful read", async ({page}) => {
  await signedIn(page);
  let releaseOld;
  const holdOld = new Promise(resolve => { releaseOld = resolve; });
  let signalOld;
  const oldStarted = new Promise(resolve => { signalOld = resolve; });
  await page.route("**/v1/admin/tenants/*/usage", async route => {
    const path = new URL(route.request().url()).pathname;
    const old = path.includes("/sample-studio/");
    if (old) {
      signalOld();
      await holdOld;
    }
    const count = old ? 901 : 7;
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({
      usage:{tenant_id:old ? "sample-studio" : "sample-labs",period_utc:"2026-11",requests_recorded:count,monthly_allowance:1000,
        remaining_before_allowance:1000-count,overage_policy:"block",
        usage_threshold:{state:"within_limit",utilization_percent_floor:0,policy:"blocks_at_saved_cap"},
        quota_pacing:{state:"stale_or_unknown_metadata",projected_month_end_requests_floor:null,
          record_metadata_freshness:"unavailable"},
        historical_volume:{state:"unavailable"},record_updated_at:null,
        source:"Synthetic tenant usage"},
      read_audit_event_id:"a".repeat(64)})});
  });
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Usage",exact:true}).click();
  await oldStarted;
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-labs"})
    .getByRole("button",{name:"Usage",exact:true}).click();
  await expect(page.locator("#usage-details")).toContainText("sample-labs · UTC 2026-11 · Recorded: 7");
  const lateResponse = page.waitForResponse(response =>
    response.url().includes("/sample-studio/usage"));
  releaseOld();
  await lateResponse;
  await page.waitForTimeout(80);
  await expect(page.locator("#usage-details")).toContainText("sample-labs · UTC 2026-11 · Recorded: 7");
  await expect(page.locator("#usage-details")).not.toContainText("901");
});

test("late failed Stripe check cannot replace newer tenant invoice view", async ({page}) => {
  await signedIn(page);
  let releaseOld;
  const holdOld = new Promise(resolve => { releaseOld = resolve; });
  let signalOld;
  const oldStarted = new Promise(resolve => { signalOld = resolve; });
  await page.route("**/v1/admin/subscriptions/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/v1/admin/subscriptions/sample-studio") {
      signalOld();
      await holdOld;
      await route.fulfill({status:503,contentType:"application/json",
        body:JSON.stringify({detail:"Synthetic Stripe outage"})});
    } else if (path === "/v1/admin/subscriptions/sample-labs/invoices") {
      await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({
        tenant_id:"sample-labs",history:{verified:true,status:"verified",count:1,has_more:false,
            page_number:1,status_filter:null,next_cursor:null,
          invoices:[{status:"paid",currency:"usd",created_utc:"2026-11-20T12:00:00Z",
            amount_due_minor:700,amount_paid_minor:700,amount_remaining_minor:0,
            collection_method:"charge_automatically",attempted:true,
            attempt_count:1,next_payment_attempt_utc:null}]},
        read_audit_event_id:"a".repeat(64)})});
    } else {
      await route.fallback();
    }
  });
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Verify Stripe",exact:true}).click();
  await oldStarted;
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-labs"})
    .getByRole("button",{name:"Invoices",exact:true}).click();
  await expect(page.locator("#invoice-details")).toContainText("sample-labs");
  await expect(page.locator("#invoice-details")).toContainText("paid USD due 700");
  const lateResponse = page.waitForResponse(response =>
    response.url().endsWith("/v1/admin/subscriptions/sample-studio"));
  releaseOld();
  await lateResponse;
  await page.waitForTimeout(80);
  await expect(page.locator("#billing-details")).not.toContainText("sample-studio");
  await expect(page.locator("#status")).not.toContainText("503");
  await expect(page.locator("#invoice-details")).toContainText("sample-labs");
});

test("owner Usage displays an actual saved cap breach even when metadata is stale", async ({page}) => {
  const fixture = await signedIn(page, {override: request =>
    /^\/v1\/admin\/tenants\/[^/]+\/usage$/.test(request.path)
      ? {status:200, contentType:"application/json", body:JSON.stringify({
          usage:{tenant_id:"sample-studio",
            period_utc:"2026-11", requests_recorded:105, monthly_allowance:100,
            remaining_before_allowance:0, overage_policy:"block",
            usage_threshold:{state:"over_limit",utilization_percent_floor:105,
              policy:"blocks_at_saved_cap",persisted_alert:false},
            quota_pacing:{state:"already_over_limit",projected_month_end_requests_floor:null,
              record_metadata_freshness:"stale_record_metadata",
              calendar_days_elapsed:2,calendar_days_in_month:30,persisted_alert:false},
            historical_volume:{state:"unavailable",persisted_alert:false},
            record_updated_at:"2026-11-01T00:00:00Z",
            aws_cost_usd:null,stripe_invoice_usd:null,
            source:"Synthetic saved tenant record"},
          read_audit_event_id:"a".repeat(64)})} : null
  });
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button", {name:"Usage", exact:true}).click();
  const details = page.locator("#usage-details");
  await expect(details).toContainText("Recorded quota signal: over limit (105%)");
  await expect(details).toContainText("Quota pacing: already over limit");
  await expect(details).toContainText("Record metadata: stale record metadata");
  await expect(details).toContainText("Projected recorded month-end requests: Unavailable");
  await expect(details).toContainText("AWS costs: Unavailable");
  expect(fixture.calls.some(call => call.path.endsWith("/usage") &&
    call.headers["x-guardoath-admin-session"] === fixture.token &&
    call.headers["x-guardoath-key"] === undefined)).toBe(true);
});

test("owner Billing shows verified collection attempt metadata without claiming failed payment", async ({page}) => {
  const fixture = await signedIn(page, {override: request =>
    /^\/v1\/admin\/subscriptions\/[^/]+\/invoices$/.test(request.path)
      ? {status:200, contentType:"application/json", body:JSON.stringify({
          tenant_id:"sample-studio",history:{verified:true,status:"verified",count:1,has_more:false,
            page_number:1,status_filter:null,next_cursor:null,
            read_at_utc:"2026-11-21T12:00:00Z",invoices:[{
              status:"open",currency:"usd",created_utc:"2026-11-20T12:00:00Z",
              amount_due_minor:2500,amount_paid_minor:0,amount_remaining_minor:2500,
              collection_method:"charge_automatically",attempted:true,attempt_count:2,
              next_payment_attempt_utc:"2026-11-23T12:00:00Z"}],
            payment_review:{scope:"verified_first_page_only",
              open_with_balance_count:1,open_with_recorded_next_attempt_count:1,
              uncollectible_count:0,open_balance_by_currency_minor:{usd:2500},
              complete_history:true}},
          read_audit_event_id:"a".repeat(64)})} : null});
  await page.locator("#nav-tenants").click();
  const row = page.locator("#tenant-rows tr").filter({hasText:"sample-studio"});
  await row.getByRole("button",{name:"Invoices",exact:true}).click();
  const output = page.locator("#invoice-details");
  await expect(output).toContainText("open USD due 2500");
  await expect(output).toContainText("Collection: charge_automatically");
  await expect(output).toContainText("Attempted: Yes");
  await expect(output).toContainText("attempts recorded: 2");
  await expect(output).toContainText("next attempt recorded: 2026-11-23T12:00:00Z");
  await expect(output).toContainText("Recorded next automatic-payment attempts (shown page): 1");
  await expect(output).toContainText("not proof of payment failure");
  expect(fixture.calls.some(call => call.path.endsWith("/invoices") &&
    call.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
});

test("owner rejects inconsistent verified invoice next-attempt count", async ({page}) => {
  const fixture = await signedIn(page, {override: request =>
    request.path === "/v1/admin/subscriptions/sample-studio/invoices"
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",history:{verified:true,status:"verified",count:1,has_more:false,
            page_number:1,status_filter:null,next_cursor:null,
            read_at_utc:"2026-11-23T12:00:00Z",
            invoices:[{status:"open",currency:"usd",created_utc:"2026-11-20T12:00:00Z",
              amount_due_minor:2500,amount_paid_minor:0,amount_remaining_minor:2500,
              collection_method:"charge_automatically",attempted:true,attempt_count:2,
              next_payment_attempt_utc:"2026-11-24T12:00:00Z"}],
            payment_review:{scope:"verified_first_page_only",
              open_with_balance_count:1,open_with_recorded_next_attempt_count:2,
              uncollectible_count:0,open_balance_by_currency_minor:{usd:2500},
              complete_history:true}},
          read_audit_event_id:"a".repeat(64)})} : null});
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Invoices",exact:true}).click();
  await expect(page.locator("#invoice-details")).toContainText(
    "Invoice verification unavailable; no billing changes");
  expect(fixture.calls.filter(call => call.path.startsWith("/v1/admin/subscriptions/") &&
    call.method !== "GET")).toHaveLength(0);
});

test("verified inactive Stripe disagreement appears in owner Billing without inventing paid access", async ({page}) => {
  const fixture = await signedIn(page,{override: request =>
    /^\/v1\/admin\/subscriptions\/[^/]+$/.test(request.path)
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",subscription:{verified:true,status:"canceled",verified_at:"2026-11-21T12:00:00Z",
            reconciliation:{level:"review",code:"saved_nonactive_state_differs_from_provider",
              next_step:"Review trusted Stripe reconciliation; do not grant access or rewrite saved billing state"}},
          read_audit_event_id:"a".repeat(64)})} : null});
  await page.locator("#nav-tenants").click();
  const row = page.locator("#tenant-rows tr").filter({hasText:"sample-studio"});
  await row.getByRole("button",{name:"Verify Stripe",exact:true}).click();
  const output = page.locator("#billing-details");
  await expect(page.locator("#section-billing")).toBeVisible();
  await expect(output).toContainText("Provider: canceled");
  await expect(output).toContainText("Reconciliation: review (saved_nonactive_state_differs_from_provider)");
  await expect(output).toContainText("do not grant access or rewrite saved billing state");
  await expect(output).toContainText("Next invoice amount: Unavailable");
  expect(fixture.calls.some(call => call.path === "/v1/admin/subscriptions/sample-studio"
    && call.method === "GET" && call.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
  expect((await output.textContent())).not.toContain("subscription_entitlement: true");
});

test("owner billing reveals Stripe collection pause even while subscription status is active", async ({page}) => {
  const fixture = await signedIn(page,{override: request =>
    request.path === "/v1/admin/subscriptions/sample-studio"
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",subscription:{verified:true,status:"active",verified_at:"2026-11-23T08:00:00Z",
            collection_status:"paused",collection_behavior:"keep_as_draft",
            collection_resumes_at_utc:null,
            reconciliation:{level:"review",code:"stripe_payment_collection_paused",
              next_step:"Review Stripe payment collection pause; active subscription status does not prove payments are collecting"}},
          read_audit_event_id:"a".repeat(64)})} : null});
  await page.locator("#nav-tenants").click();
  const row = page.locator("#tenant-rows tr").filter({hasText:"sample-studio"});
  await row.getByRole("button",{name:"Verify Stripe",exact:true}).click();
  const output = page.locator("#billing-details");
  await expect(output).toContainText("Provider: active");
  await expect(output).toContainText("Reconciliation: review (stripe_payment_collection_paused)");
  await expect(output).toContainText("Payment collection: Paused (keep_as_draft; resume UTC: Not scheduled)");
  await expect(output).toContainText("Next invoice amount: Unavailable");
  expect(fixture.calls.some(call => call.path === "/v1/admin/subscriptions/sample-studio"
    && call.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
});

test("unavailable verified Stripe read is visible and never claims paid entitlement", async ({page}) => {
  const fixture = await signedIn(page,{override: request =>
    request.path === "/v1/admin/subscriptions/sample-studio"
      ? {status:503,contentType:"application/json",body:JSON.stringify({
          detail:"Subscription verification unavailable"})} : null});
  await page.locator("#nav-tenants").click();
  const row = page.locator("#tenant-rows tr").filter({hasText:"sample-studio"});
  await row.getByRole("button",{name:"Verify Stripe",exact:true}).click();
  await expect(page.locator("#section-billing")).toBeVisible();
  const output = page.locator("#billing-details");
  await expect(output).toContainText("Stripe verification unavailable; no entitlement change");
  await expect(output).not.toContainText("Provider: active");
  expect(fixture.calls.some(call => call.method === "GET" &&
    call.path === "/v1/admin/subscriptions/sample-studio" &&
    call.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
});

test("mobile owner workspace keeps all sections accessible without document overflow", async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await signedIn(page);
  for (const section of sections) {
    await page.locator("#nav-"+section).click();
    await expect(page.locator("#section-"+section)).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  }
});

test("native MFA completion surfaces protected-data failure in visible workspace", async ({page}) => {
  await installAdminFixture(page,{nativeMfa:true,override:request => request.path === "/v1/admin/overview"
    ? {status:503,contentType:"application/json",body:JSON.stringify({detail:"Read audit unavailable"})} : null});
  await page.goto("http://127.0.0.1:8777/admin/login");
  await expect(page.locator("#native-auth")).toBeVisible();
  await page.locator("#native-email").fill("owner@example.invalid");
  await page.locator("#native-password").fill("synthetic-fixture-password");
  await page.locator("#native-start").click();
  await expect(page.locator("#native-challenge")).toBeVisible();
  await expect(page.locator("#native-password")).toHaveValue("");
  await page.locator("#native-code").fill("123456");
  await page.locator("#native-finish").click();
  await expect(page.locator("#app")).toBeVisible();
  await expect(page.locator("#login")).toBeHidden();
  await expect(page.locator("#status")).toBeVisible();
  await expect(page.locator("#status")).toContainText("Request failed (503)");
  await expect(page.locator("#native-code")).toHaveValue("");
});

test("directory paging, filtering and rate failure do not leave stale users", async ({page}) => {
  const fixture = await signedIn(page);
  await page.locator("#nav-users").click();
  await expect(page.locator("#user-rows tr")).toHaveCount(2);
  await page.locator("#user-next").click();
  await expect(page.locator("#user-rows tr")).toHaveCount(1);
  await expect(page.locator("#user-next")).toBeDisabled();
  expect(fixture.calls.some(c=>c.path==="/v1/admin/users" && c.url.searchParams.get("cursor")==="fixture-user-next")).toBe(true);
  await page.locator("#user-search").fill("a");
  await page.locator("#user-search-go").click();
  await expect(page.locator("#user-rows")).toContainText("a***@example.invalid");
  fixture.override = request => request.path === "/v1/admin/users"
    ? {status:429,contentType:"application/json",body:JSON.stringify({detail:"Rate limited"})} : null;
  await page.locator("#user-search-go").click();
  await expect(page.locator("#user-status")).toContainText("request limit reached (429)");
  await expect(page.locator("#user-rows tr")).toHaveCount(0);
  await expect(page.locator("#user-next")).toBeDisabled();
});


test("owner directory ignores out-of-order Cognito success from an older filter", async ({page}) => {
  await signedIn(page);
  await page.locator("#nav-users").click();
  let releaseOlder, startedOlder, deliveredOlder;
  const hold = new Promise(resolve => releaseOlder=resolve);
  const started = new Promise(resolve => startedOlder=resolve);
  const delivered = new Promise(resolve => deliveredOlder=resolve);
  const response = name => ({status:200,contentType:"application/json",body:JSON.stringify({
    status:"ok",users:[{username:name,email_hint:name+"***@example.invalid",status:"CONFIRMED"}],
    returned:1,next_cursor:null,source:"synthetic Cognito page"})});
  await page.route("**/v1/admin/users?*", async route => {
    const q = new URL(route.request().url()).searchParams.get("query");
    if (q === "a") {
      startedOlder();
      await hold;
      await route.fulfill(response("a"));
      deliveredOlder();
    } else if (q === "b") await route.fulfill(response("b"));
    else await route.fallback();
  });
  await page.locator("#user-search").fill("a");
  await page.locator("#user-search-go").click();
  await started;
  await page.locator("#user-search").fill("b");
  await page.locator("#user-search-go").click();
  await expect(page.locator("#user-rows")).toContainText("b***@example.invalid");
  releaseOlder();
  await delivered;
  await page.waitForTimeout(50);
  await expect(page.locator("#user-rows")).toContainText("b***@example.invalid");
  await expect(page.locator("#user-rows")).not.toContainText("a***@example.invalid");
  await expect(page.locator("#user-next")).toBeDisabled();
});

test("tenant directory ignores a late failed filter and clears rows on a current failure", async ({page}) => {
  const fixture = await signedIn(page);
  await page.locator("#nav-tenants").click();
  let releaseOlder, startedOlder, deliveredOlder;
  const hold = new Promise(resolve => releaseOlder=resolve);
  const started = new Promise(resolve => startedOlder=resolve);
  const delivered = new Promise(resolve => deliveredOlder=resolve);
  await page.route("**/v1/admin/tenants?*", async route => {
    const q = new URL(route.request().url()).searchParams.get("search");
    if (q === "studio") {
      startedOlder();
      await hold;
      await route.fulfill({status:503,contentType:"application/json",body:'{"detail":"stale provider error"}'});
      deliveredOlder();
    } else if (q === "labs") await route.fulfill({
      status:200,contentType:"application/json",body:JSON.stringify({
        status:"ok",tenants:[fixture.tenants[1]],returned:1,scanned:1,
        next_cursor:null,source:"synthetic tenant records"})});
    else if (q === "unknown") await route.fulfill({
      status:503,contentType:"application/json",body:'{"detail":"current provider error"}'});
    else await route.fallback();
  });
  await page.locator("#search").fill("studio");
  await page.locator("#search-go").click();
  await started;
  await page.locator("#search").fill("labs");
  await page.locator("#search-go").click();
  await expect(page.locator("#tenant-rows")).toContainText("sample-labs");
  releaseOlder();
  await delivered;
  await page.waitForTimeout(50);
  await expect(page.locator("#tenant-rows")).toContainText("sample-labs");
  await expect(page.locator("#tenant-status")).not.toContainText("Request failed");
  await page.locator("#search").fill("unknown");
  await page.locator("#search-go").click();
  await expect(page.locator("#tenant-status")).toContainText("Request failed (503)");
  await expect(page.locator("#tenant-rows tr")).toHaveCount(0);
  await expect(page.locator("#next")).toBeDisabled();
});

test("Cognito account status disables repeat submission during independent identity preflight", async ({page}) => {
  const fixture = await signedIn(page,{features:{cognito_user_status_changes:true}});
  await page.locator("#nav-users").click();
  let releaseLookup, signalLookup;
  const held = new Promise(resolve => releaseLookup=resolve);
  const started = new Promise(resolve => signalLookup=resolve);
  let calls = 0;
  await page.route("**/v1/admin/users/lookup", async route => {
    calls += 1;
    signalLookup();
    await held;
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",user:{username:"user-fixture",enabled:true,status:"CONFIRMED"}})});
  });
  page.on("dialog", dialog => dialog.dismiss());
  await page.locator("#user-change-email").fill("someone@example.invalid");
  await page.locator("#user-change").click();
  await started;
  await expect(page.locator("#user-change")).toBeDisabled();
  await page.locator("#user-change").evaluate(button => button.click());
  expect(calls).toBe(1);
  releaseLookup();
  await expect(page.locator("#user-change")).toBeEnabled();
  expect(fixture.calls.filter(c => c.path === "/v1/admin/users/status")).toHaveLength(0);
});

for (const status of [401,403]) test(`revoked owner response ${status} clears private UI and returns to login`, async ({page}) => {
  const fixture = await signedIn(page);
  await page.locator("#nav-users").click();
  await page.locator("#user-exact-email").fill("private@example.invalid");
  fixture.override = request => request.path.startsWith("/v1/admin/") && !request.path.startsWith("/v1/admin/auth/")
    ? {status,contentType:"application/json",body:JSON.stringify({detail:"Access revoked"})} : null;
  await page.locator("#refresh").click();
  await expect(page.locator("#login")).toBeVisible();
  await expect(page.locator("#app")).toBeHidden();
  await expect(page.locator("#login-message")).toContainText("expired or access revoked");
  for (const selector of ["#tenant-rows tr","#user-rows tr","#categories details","#metrics .metric"])
    await expect(page.locator(selector)).toHaveCount(0);
  await expect(page.locator("#user-exact-email")).toHaveValue("");
});

test("provider text remains text and explicit logout clears all private panels", async ({page}) => {
  const fixture = await installAdminFixture(page);
  const injected = '<img src=x onerror="document.body.dataset.injected=1">';
  fixture.tenants[0].display_name = injected;
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-tenants").click();
  await expect(page.locator("#tenant-rows")).toContainText(injected);
  await expect(page.locator("#tenant-rows img")).toHaveCount(0);
  expect(await page.locator("body").getAttribute("data-injected")).toBeNull();
  await page.locator("#nav-audit").click();
  await page.locator("#audit-id").fill(fixture.receipt);
  await page.locator("#audit-go").click();
  await expect(page.locator("#audit-result")).toContainText("HMAC integrity verified");
  await page.locator("#logout").click();
  await expect(page.locator("#login")).toBeVisible();
  await expect(page.locator("#audit-id")).toHaveValue("");
  await expect(page.locator("#audit-result")).toBeEmpty();
  await expect(page.locator("#tenant-rows tr")).toHaveCount(0);
});



test("catalog editor refuses invisible title before any authenticated preview request", async ({page}) => {
  const fixture = await signedIn(page,{features:{catalog_preview:true,catalog_drafts:true}});
  await selectCatalogDescription(page);
  for (const control of ["\u0007", "\u0085", "\u202e", "\u200b", "\u2028", "\u2029"]) {
    for (const field of ["#proposal-title", "#proposal-use-case"]) {
      await page.locator("#proposal-title").fill("AAA BBB");
      await page.locator("#proposal-use-case").fill("AAA BBB");
      const value = "AAA" + control + "BBB";
      await page.locator(field).fill(value);
      await expect(page.locator(field)).toHaveValue(value);
      await page.locator("#proposal-go").click();
      await expect(page.locator("#proposal-result")).toContainText("invisible or bidirectional control characters");
      await expect(page.locator("#proposal-save")).toBeDisabled();
      expect(fixture.calls.filter(call => call.path === "/v1/admin/catalog/preview")).toHaveLength(0);
    }
  }
});

test("catalog preview and draft accept visible international text after rejecting an invisible reason", async ({page}) => {
  const draftId = "d".repeat(64);
  const fixture = await signedIn(page, {
    features:{catalog_drafts:true},
    override: request => request.method === "POST" && request.path === "/v1/admin/catalog/drafts"
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          status:"draft_saved",persisted:true,published:false,
          tenant_policies_changed:false,draft_id:draftId})} : null
  });
  await selectCatalogDescription(page);
  const title = "Privacy review — Café 東京 🔒";
  const useCase = "Vérifier les données — مرحبًا — שלום — e\u0301";
  await page.locator("#proposal-title").fill(title);
  await page.locator("#proposal-use-case").fill(useCase);
  await page.locator("#proposal-go").click();
  await expect(page.locator("#proposal-result")).toContainText("Preview only — NOT saved or published");
  await expect(page.locator("#proposal-save")).toBeEnabled();
  const previews = fixture.calls.filter(call => call.path === "/v1/admin/catalog/preview");
  expect(previews).toHaveLength(1);
  expect(previews[0].body).toMatchObject({title,use_case:useCase,rule_id:"pii.email"});
  expect(previews[0].headers["x-guardoath-admin-session"]).toBe(fixture.token);

  let reason = "AAAAA\u202eBBBBB";
  page.on("dialog", async dialog => {
    if (dialog.type() === "prompt") await dialog.accept(reason);
    else await dialog.accept();
  });
  await page.locator("#proposal-save").click();
  await expect(page.locator("#proposal-result")).toContainText("Draft reason cannot contain invisible or bidirectional control characters");
  expect(fixture.calls.filter(call => call.path === "/v1/admin/catalog/drafts")).toHaveLength(0);

  reason = "Reviewed visible metadata — Café 東京 مرحبًا שלום 🔒";
  await page.locator("#proposal-save").click();
  await expect(page.locator("#proposal-result")).toContainText("Signed draft saved");
  await expect(page.locator("#proposal-result")).toContainText("NOT reviewed or published");
  await expect(page.locator("#proposal-save")).toBeDisabled();
  await expect(page.locator("#draft-id")).toHaveValue(draftId);
  const drafts = fixture.calls.filter(call => call.path === "/v1/admin/catalog/drafts");
  expect(drafts).toHaveLength(1);
  expect(drafts[0].body).toMatchObject({title,use_case:useCase,reason,rule_id:"pii.email"});
  expect(drafts[0].headers["x-guardoath-admin-session"]).toBe(fixture.token);
});

test("owner reviewed catalog metadata manifest is visible only after signed review", async ({page}) => {
  const draftId = "d".repeat(64), reviewId = "e".repeat(64);
  const baseline = "b".repeat(64), proposal = "c".repeat(64), candidate = "f".repeat(64);
  let corruptRollback = false;
  const response = data => ({status:200,contentType:"application/json",body:JSON.stringify(data)});
  const fixture = await signedIn(page, {
    features:{catalog_drafts:true,catalog_review_receipts:true},
    override: request => {
      if (request.method === "GET" && request.path === "/v1/admin/catalog/drafts/"+draftId)
        return response({draft:{draft_id:draftId,rule_id:"pii.email",published:false,
          baseline_sha256:baseline,proposal_sha256:proposal},stale:false,ready_to_publish:false});
      if (request.method === "POST" && request.path === "/v1/admin/catalog/drafts/"+draftId+"/review")
        return response({status:"review_recorded",published:false,persisted:true,reviewed:true,
          ready_to_publish:false,review_id:reviewId,draft_id:draftId,
          baseline_sha256:baseline,proposal_sha256:proposal});
      if (request.method === "GET" && request.path === "/v1/admin/catalog/reviews/"+reviewId)
        return response({review:{review_id:reviewId,draft_id:draftId,
          baseline_sha256:baseline,proposal_sha256:proposal,status:"review_recorded",
          published:false},published:false,ready_to_publish:false,stale:false,source_current:true});
      if (request.method === "GET" && request.path === "/v1/admin/catalog/reviews/"+reviewId+"/manifest")
        return response({status:"metadata_manifest_preview_only",review_id:reviewId,draft_id:draftId,
          reviewed_source_sha256:baseline,reviewed_proposal_sha256:proposal,
          candidate_metadata_sha256:candidate,rollback_source_sha256:baseline,
          registry_version:"3",candidate_registry_version:"4",category_id:"privacy",
          check_id:"pii",rule_id:"pii.email",changed_fields:["title"],
          changed_metadata:{title:{before:"Email rule",after:"Revised email rule"}},
          published:false,
          ready_to_publish:false,tenant_policies_changed:false,detector_behavior_changed:false,
          source_current:true,acceptance_tests_run:false,
          reversible_patch:{
            kind:"reviewed_metadata_source_plan_only",
            target:{category_id:"privacy",check_id:"pii",rule_id:"pii.email"},
            forward:{expected_registry_version:"3",candidate_registry_version:"4",
              expected_metadata_sha256:baseline,candidate_metadata_sha256:candidate,
              changes:{title:{before:"Email rule",after:"Revised email rule"}}},
            rollback:{expected_registry_version:"4",candidate_registry_version:"3",
              expected_metadata_sha256:candidate,
              candidate_metadata_sha256:corruptRollback ? "0".repeat(64) : baseline,
              changes:{title:{before:"Revised email rule",after:"Email rule"}}},
            round_trip_verified:true,executable_source_committed:false,
            published:false,tenant_policies_changed:false
          },
          release_kind:"review_only_projection_not_executable_source"});
      return null;
    }
  });
  await page.locator("#nav-catalog").click();
  await expect(page.locator("#review-manifest")).toBeDisabled();
  await expect(page.locator("#manifest-export")).toBeDisabled();
  await page.locator("#draft-id").fill(draftId);
  await page.locator("#draft-inspect").click();
  await expect(page.locator("#draft-review")).toBeEnabled();
  await expect(page.locator("#review-manifest")).toBeDisabled();
  let reviewReason = "AAAAA\u200bBBBBB";
  page.on("dialog", async dialog => {
    if (dialog.type() === "prompt") {
      if (dialog.message().includes("type REVIEW")) await dialog.accept("REVIEW "+draftId.slice(-8));
      else await dialog.accept(reviewReason);
    } else await dialog.accept();
  });
  await page.locator("#draft-review").click();
  await expect(page.locator("#draft-result")).toContainText("Review reason cannot contain invisible or bidirectional control characters");
  await expect(page.locator("#review-manifest")).toBeDisabled();
  expect(fixture.calls.filter(call => call.method === "POST" &&
    call.path === "/v1/admin/catalog/drafts/"+draftId+"/review")).toHaveLength(0);
  reviewReason = "Reviewed the current documented metadata";
  await page.locator("#draft-review").click();
  await expect(page.locator("#review-manifest")).toBeEnabled();
  await page.locator("#review-manifest").click();
  await expect(page.locator("#review-manifest-result")).toContainText("NOT published or executable");
  await expect(page.locator("#review-manifest-result")).toContainText(candidate);
  await expect(page.locator("#review-manifest-result")).toContainText("forward/rollback round-trip verified");
  await expect(page.locator("#manifest-export")).toBeEnabled();
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.locator("#manifest-export").click()
  ]);
  expect(download.suggestedFilename()).toBe("guardoath-catalog-plan-"+reviewId.slice(0,12)+".json");
  const chunks = [];
  for await (const chunk of await download.createReadStream()) chunks.push(chunk);
  const artifact = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  expect(artifact).toMatchObject({
    format:"guardoath.reviewed-catalog-metadata-source-plan.v1",
    status:"review_only_not_published", review_id:reviewId,draft_id:draftId,
    reviewed_source_sha256:baseline,candidate_metadata_sha256:candidate,
    rollback_source_sha256:baseline,
    published:false, executable_source_committed:false,
    acceptance_tests_run:false, hashes_are_not_signatures:true
  });
  expect(artifact.forward.changes.title).toEqual({before:"Email rule",after:"Revised email rule"});
  expect(artifact.rollback.changes.title).toEqual({before:"Revised email rule",after:"Email rule"});
  expect(JSON.stringify(artifact)).not.toContain(fixture.token);
  expect(JSON.stringify(artifact)).not.toContain("read_audit_event_id");
  corruptRollback = true;
  await page.locator("#draft-inspect").click();
  await expect(page.locator("#draft-review")).toBeEnabled();
  await page.locator("#draft-review").click();
  await expect(page.locator("#review-manifest")).toBeEnabled();
  await page.locator("#review-manifest").click();
  await expect(page.locator("#review-manifest-result")).toContainText("integrity unavailable");
  await expect(page.locator("#manifest-export")).toBeDisabled();
  expect(fixture.calls.some(c => c.path.endsWith("/manifest") &&
    c.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
  await expect(page.locator("#review-manifest")).toBeDisabled();
  await page.locator("#logout").click();
  await expect(page.locator("#review-manifest-result")).toBeEmpty();
  await expect(page.locator("#manifest-export")).toBeDisabled();
});


test("signed action live comparison uses owner-bound POST and clears email on sign-out", async ({page}) => {
  const key = "accountaction00000001";
  const intent = "b".repeat(64), outcome = "c".repeat(64);
  const fixture = await signedIn(page,{
    features:{cognito_action_receipts:true},
    override: request => request.path === "/v1/admin/users/status/receipt" && request.method === "POST"
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          status:"outcome_recorded",intent_event_id:intent,outcome_event_id:outcome,
          enabled_at_verification:false,current_provider_state_verified:true,current_enabled:true,
          current_matches_recorded_outcome:false,current_observed_at_utc:"2026-11-21T09:00:00Z"
        })} : null
  });
  await page.locator("#nav-users").click();
  await page.locator("#user-action-key").fill(key);
  await page.locator("#user-action-email").fill("person@example.com");
  await page.locator("#user-action-inspect").click();
  await expect(page.locator("#user-action-result")).toContainText("current enabled state DIFFERS");
  await expect(page.locator("#user-action-result")).toContainText("not proof of the historical provider action");
  const call = fixture.calls.find(c => c.path === "/v1/admin/users/status/receipt");
  expect(call.method).toBe("POST");
  expect(call.headers["x-guardoath-admin-session"]).toBe(fixture.token);
  expect(call.headers["x-guardoath-key"]).toBeUndefined();
  expect(call.body).toEqual({idempotency_key:key,email:"person@example.com"});
  await page.locator("#logout").click();
  await expect(page.locator("#user-action-email")).toHaveValue("");
  await expect(page.locator("#user-action-result")).toBeEmpty();
});


test("catalog exposes only verified source history and clears it on sign-out", async ({page}) => {
  const fixture = await signedIn(page, {
    override: (request, local) => {
      if (request.path !== "/v1/admin/catalog") return null;
      return {status:200,contentType:"application/json",body:JSON.stringify({
        ...local.catalog,
        metadata_releases:{
          status:"verified_source_history",source:"Synthetic shipped source",
          registry_version:"4",release_count:1,has_older_releases:false,
          source_chain_verified:true,live_acceptance_verified:null,
          releases:[{registry_version:"4",category_id:"privacy",check_id:"pii",
            rule_id:"pii.email",changed_fields:["title"]}]},
        registry_version:"4"
      })};
    }
  });
  await page.locator("#nav-catalog").click();
  await expect(page.locator("#catalog-release-summary")).toContainText("1 shipped releases");
  await expect(page.locator("#catalog-release-entries li")).toHaveCount(1);
  await expect(page.locator("#catalog-release-entries")).toContainText("pii.email");
  await page.locator("#logout").click();
  await expect(page.locator("#catalog-release-entries li")).toHaveCount(0);
  await expect(page.locator("#catalog-release-summary")).toBeEmpty();
  expect(fixture.calls.filter(x => x.path === "/v1/admin/catalog")).toHaveLength(1);
});

test("catalog does not label unverified history as zero releases", async ({page}) => {
  await signedIn(page, {override: (request, local) =>
    request.path === "/v1/admin/catalog"
      ? {status:200,contentType:"application/json",
          body:JSON.stringify({...local.catalog,
            metadata_releases:{status:"unavailable",release_count:null}})}
      : null});
  await page.locator("#nav-catalog").click();
  await expect(page.locator("#catalog-release-summary")).toContainText("unavailable");
  await expect(page.locator("#catalog-release-entries li")).toHaveCount(0);
});

test("owner rule editor reads saved stage and override before a revision-conditional write", async ({page}) => {
  const receipt = "a".repeat(64);
  const fixture = await signedIn(page, {
    features:{tenant_rule_changes:true},
    override: request => {
      if (request.path === "/v1/admin/tenants/sample-studio/rule-action" &&
          request.method === "GET")
        return {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",revision:4,rule_id:"pii.email",check_id:"pii",
          stage:"input",check_action:null,rule_action:"off",eligibility:"check_off",
          read_audit_event_id:receipt})};
      if (request.path === "/v1/admin/tenants/sample-studio/rule-action" &&
          request.method === "POST")
        return {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",revision:5,stage:"input",
          rule_id:"pii.email",action:"block",audit_event_id:receipt})};
      if (request.path === "/v1/admin/audit/" + receipt)
        return {status:200,contentType:"application/json",body:JSON.stringify({audit:{
          status:"ok",integrity_status:"signed_verified",event_id:receipt,
          operation:"tenant.rule_action",tenant_id:"sample-studio",
          prior_revision:4,new_revision:5,
          change:{field:"rule_action",rule_id:"pii.email",stage:"input",
            before:"off",after:"block"}}})};
      return null;
    }
  });
  const prompts = [];
  page.on("dialog", async dialog => {
    prompts.push(dialog.message());
    if (dialog.type() === "confirm") return dialog.accept();
    const message = dialog.message();
    return dialog.accept(
      message.startsWith("Tenant ID") ? "sample-studio" :
      message.startsWith("Stage:") ? "input" :
      message.startsWith("Action:") ? "block" :
      message.startsWith("Type tenant") ? "sample-studio" :
      message.startsWith("Audit reason") ? "Reviewed tenant policy with known Off check" : "");
  });
  await page.locator("#nav-catalog").click();
  const category = page.locator("#categories > details").first();
  await category.locator(":scope > summary").click();
  const check = category.locator(":scope > details").first();
  await check.locator(":scope > summary").click();
  await check.getByRole("button",{name:"Set tenant rule"}).click();
  await expect(page.locator("#status")).toContainText("Tenant rule saved");
  const reads = fixture.calls.filter(c => c.path ===
    "/v1/admin/tenants/sample-studio/rule-action" && c.method === "GET");
  const writes = fixture.calls.filter(c => c.path ===
    "/v1/admin/tenants/sample-studio/rule-action" && c.method === "POST");
  expect(reads).toHaveLength(1);
  expect(reads[0].url.searchParams.get("stage")).toBe("input");
  expect(reads[0].url.searchParams.get("rule_id")).toBe("pii.email");
  expect(writes).toHaveLength(1);
  expect(writes[0].body).toMatchObject({expected_revision:4,stage:"input",
    rule_id:"pii.email",action:"block"});
  expect(prompts.join(" ")).toContain("The check itself is OFF");
  expect(fixture.calls.some(c => c.path === "/v1/admin/tenants/sample-studio" &&
    c.method === "GET")).toBe(false);
});

test("wrong tenant and unavailable signed rule audit never report owner mutation complete", async ({page}) => {
  const id = "b".repeat(64);
  let auditMode = "wrong_tenant";
  const fixture = await signedIn(page, {
    features:{tenant_rule_changes:true},
    override: request => {
      if (request.path === "/v1/admin/tenants/sample-studio/rule-action" && request.method === "GET")
        return {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",revision:4,rule_id:"pii.email",stage:"input",
          check_action:null,rule_action:"off",eligibility:"check_off"})};
      if (request.path === "/v1/admin/tenants/sample-studio/rule-action" && request.method === "POST")
        return {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",revision:5,stage:"input",
          rule_id:"pii.email",action:"block",audit_event_id:id})};
      if (request.path === "/v1/admin/audit/" + id)
        return auditMode === "outage"
          ? {status:503,contentType:"application/json",body:JSON.stringify({detail:"Unavailable"})}
          : {status:200,contentType:"application/json",body:JSON.stringify({audit:{
            status:"ok",integrity_status:"signed_verified",event_id:id,
            operation:"tenant.rule_action",tenant_id:"sample-labs",
            prior_revision:4,new_revision:5,
            change:{field:"rule_action",rule_id:"pii.email",stage:"input",
              before:"off",after:"block"}}})};
      return null;
    }
  });
  page.on("dialog", async dialog => dialog.type() === "confirm" ? dialog.accept()
    : dialog.accept(dialog.message().startsWith("Tenant ID") ? "sample-studio" :
        dialog.message().startsWith("Stage:") ? "input" :
        dialog.message().startsWith("Action:") ? "block" :
        dialog.message().startsWith("Type tenant") ? "sample-studio" :
        dialog.message().startsWith("Audit reason") ? "Reviewed existing saved Off rule" : ""));
  await page.locator("#nav-catalog").click();
  const category = page.locator("#categories > details").first();
  await category.locator(":scope > summary").click();
  const check = category.locator(":scope > details").first();
  await check.locator(":scope > summary").click();
  const change = check.getByRole("button",{name:"Set tenant rule"});
  await change.click();
  await expect(page.locator("#status")).toContainText("Signed mutation audit unverified");
  await expect(page.locator("#status")).not.toContainText("Tenant rule saved");
  auditMode = "outage";
  await change.click();
  await expect(page.locator("#status")).toContainText("Request failed (503)");
  await expect(page.locator("#status")).not.toContainText("Tenant rule saved");
  expect(fixture.calls.filter(c => c.path.endsWith("/rule-action") && c.method === "POST")).toHaveLength(2);
  expect(fixture.calls.filter(c => c.path === "/v1/admin/audit/" + id)).toHaveLength(2);
});

test("rule inspection outage or cross-tenant response prevents every owner rule mutation", async ({page}) => {
  let wrongTenant = false;
  const fixture = await signedIn(page,{features:{tenant_rule_changes:true},
    override: request => request.path === "/v1/admin/tenants/sample-studio/rule-action" &&
      request.method === "GET"
      ? wrongTenant
        ? {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"usr-other",revision:7,rule_id:"pii.email",stage:"input",
          check_action:"block",rule_action:"same_as_check",
          eligibility:"configured_not_execution_evidence"})}
        : {status:503,contentType:"application/json",body:JSON.stringify({
          detail:"Policy unavailable"})}
      : null});
  page.on("dialog", async dialog => dialog.type() === "prompt"
    ? dialog.accept(dialog.message().startsWith("Stage:") ? "input" : "sample-studio")
    : dialog.accept());
  await page.locator("#nav-catalog").click();
  const category = page.locator("#categories > details").first();
  await category.locator(":scope > summary").click();
  const check = category.locator(":scope > details").first();
  await check.locator(":scope > summary").click();
  const change = check.getByRole("button",{name:"Set tenant rule"});
  await change.click();
  await expect(page.locator("#status")).toContainText("Request failed (503)");
  wrongTenant = true;
  await change.click();
  await expect(page.locator("#status")).toContainText("inspection could not be verified");
  expect(fixture.calls.filter(c => c.method === "POST" &&
    c.path.endsWith("/rule-action"))).toHaveLength(0);
});

test("owner unpaid reconciliation requires a fully matching signed audit read-back before success", async ({page}) => {
  const eventId = "b".repeat(64);
  let tamper = true;
  const fixture = await signedIn(page, {
    features:{billing_downgrade:true,audit_receipt_lookup:true},
    override:request => {
      if (request.path === "/v1/admin/subscriptions/sample-studio/downgrade" &&
          request.method === "POST") return {status:200,contentType:"application/json",body:JSON.stringify({
            tenant_id:"sample-studio",revision:5,subscription_status:"past_due",
            monthly_allowance:0,overage:"block",provider_status:"past_due",
            audit_event_id:eventId
          })};
      if (request.path === "/v1/admin/audit/" + eventId) return {
        status:200,contentType:"application/json",body:JSON.stringify({audit:{
          status:"ok",integrity_status:"signed_verified",
          event_id:tamper ? "c".repeat(64) : eventId,
          operation:"billing.downgrade",tenant_id:"sample-studio",
          prior_revision:4,new_revision:5,
          change:{field:"subscription_status",before:"active",after:"past_due",
            provider_status:"past_due",allowance_before:10000,allowance_after:0}
        }})};
      return null;
    }
  });
  page.on("dialog", dialog => dialog.accept(
    dialog.type() === "prompt" ?
      (dialog.message().startsWith("Only REVOKES") ?
        "RECONCILE sample-studio" : "Verified provider delinquency") : undefined));
  const button = page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Reconcile unpaid",exact:true});
  await page.locator("#nav-tenants").click();
  await button.click();
  await expect(page.locator("#status")).toContainText("Billing outcome audit unverified");
  await expect(page.locator("#status")).not.toContainText("Verified unpaid downgrade saved");
  expect(fixture.calls.filter(c => c.path.endsWith("/downgrade") && c.method === "POST")).toHaveLength(1);
  tamper = false;
  await button.click();
  await expect(page.locator("#status")).toContainText("Verified unpaid downgrade saved");
  expect(fixture.calls.filter(c => c.path.endsWith("/downgrade") && c.method === "POST")).toHaveLength(2);
  for (const call of fixture.calls.filter(c => c.path.endsWith("/downgrade"))) {
    expect(call.headers["x-guardoath-admin-session"]).toBe(fixture.token);
    expect(call.headers["x-guardoath-key"]).toBeUndefined();
  }
});

test("owner hides unpaid reconciliation when signed evidence or saved quota is unavailable", async ({page}) => {
  const fixture = await signedIn(page,{features:{billing_downgrade:true,audit_receipt_lookup:false}});
  await page.locator("#nav-tenants").click();
  await expect(page.getByRole("button",{name:"Reconcile unpaid",exact:true})).toHaveCount(0);
  fixture.features.audit_receipt_lookup = true;
  fixture.tenants[0].monthly_allowance = null;
  await page.locator("#refresh").click();
  await expect(page.getByRole("button",{name:"Reconcile unpaid",exact:true})).toHaveCount(0);
  expect(fixture.calls.filter(c => c.path.endsWith("/downgrade"))).toHaveLength(0);
});


test("owner billing distinguishes manual invoice collection and verified period source", async ({page}) => {
  const fixture = await signedIn(page,{override: request =>
    request.path === "/v1/admin/subscriptions/sample-studio"
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",subscription:{verified:true,status:"active",verified_at:"2026-11-23T08:00:00Z",
            collection_status:"collecting",collection_method:"send_invoice",
            current_period_end_utc:"2026-12-01T00:00:00Z",current_period_source:"subscription_item",
            reconciliation:{level:"review",code:"stripe_manual_invoice_collection",
              next_step:"Stripe sends invoices rather than charging automatically; inspect verified invoices before treating an active subscription as collected revenue"}},
          read_audit_event_id:"a".repeat(64)})} : null});
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Verify Stripe",exact:true}).click();
  const output = page.locator("#billing-details");
  await expect(output).toContainText("Reconciliation: review (stripe_manual_invoice_collection)");
  await expect(output).toContainText("Invoice collection method: Send invoice (payment unverified)");
  await expect(output).toContainText("Billing period source: Verified subscription item");
  await expect(output).toContainText("Current period ends (UTC): 2026-12-01T00:00:00Z");
  await expect(output).toContainText("Next invoice amount: Unavailable");
  expect(fixture.calls.some(call => call.path === "/v1/admin/subscriptions/sample-studio"
    && call.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
});


test("owner Billing warns of verified period-end cancellation without inventing a renewal", async ({page}) => {
  const fixture = await signedIn(page,{override: request =>
    request.path === "/v1/admin/subscriptions/sample-studio"
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",subscription:{verified:true,status:"active",verified_at:"2026-11-23T08:00:00Z",
            cancel_at_period_end:true,cancellation_status:"scheduled_for_period_end",
            cancellation_effective_utc:"2026-12-01T00:00:00Z",
            current_period_end_utc:"2026-12-01T00:00:00Z",
            collection_method:"charge_automatically",
            reconciliation:{level:"review",code:"stripe_period_end_cancellation_scheduled",
              next_step:"Subscription is scheduled to end at the current period boundary; verify future access and renewal expectations without changing saved entitlement"}},
          read_audit_event_id:"a".repeat(64)})} : null});
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Verify Stripe",exact:true}).click();
  const output = page.locator("#billing-details");
  await expect(output).toContainText("Reconciliation: review (stripe_period_end_cancellation_scheduled)");
  await expect(output).toContainText("Cancellation schedule: Scheduled at current period end (UTC: 2026-12-01T00:00:00Z)");
  await expect(output).toContainText("Next invoice amount: Unavailable");
  expect(fixture.calls.some(call => call.path === "/v1/admin/subscriptions/sample-studio" &&
    call.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
});

test("owner Billing distinguishes unknown cancellation from an explicit not-scheduled status", async ({page}) => {
  let calls = 0;
  await signedIn(page,{override: request =>
    request.path === "/v1/admin/subscriptions/sample-studio"
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",subscription:{verified:true,status:"active",verified_at:"2026-11-23T08:00:00Z",
            cancellation_status:(++calls === 1 ? "unavailable" : "not_scheduled"),
            reconciliation:{level:"consistent",code:"states_agree",next_step:"No billing mutation performed"}},
          read_audit_event_id:"a".repeat(64)})} : null});
  await page.locator("#nav-tenants").click();
  const action = page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Verify Stripe",exact:true});
  await action.click();
  await expect(page.locator("#section-billing")).toBeVisible();
  await expect(page.locator("#billing-details")).toContainText("Cancellation schedule: Unavailable");
  // Verify Stripe opens the Billing section; return to Tenants to inspect again.
  // Do not click an action in a hidden table or weaken the provider assertion.
  await page.locator("#nav-tenants").click();
  await expect(page.locator("#section-tenants")).toBeVisible();
  await action.click();
  await expect(page.locator("#billing-details")).toContainText("Cancellation schedule: Not scheduled");
});


test("owner Billing exposes custom Stripe cancellation date without calling it a renewal", async ({page}) => {
  await signedIn(page,{override: request =>
    request.path === "/v1/admin/subscriptions/sample-studio"
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          tenant_id:"sample-studio",subscription:{verified:true,status:"active",verified_at:"2026-11-23T08:00:00Z",
            cancel_at_period_end:false,cancellation_status:"scheduled_for_date",
            cancellation_effective_utc:"2026-12-02T00:00:00Z",
            reconciliation:{level:"review",code:"stripe_custom_date_cancellation_scheduled",
              next_step:"Subscription has a Stripe-verified scheduled cancellation date; review future access without changing saved entitlement"}},
          read_audit_event_id:"a".repeat(64)})} : null});
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Verify Stripe",exact:true}).click();
  const output = page.locator("#billing-details");
  await expect(output).toContainText("Reconciliation: review (stripe_custom_date_cancellation_scheduled)");
  await expect(output).toContainText("Cancellation schedule: Scheduled for provider date (UTC: 2026-12-02T00:00:00Z)");
  await expect(output).toContainText("Cancel at period end: No");
  await expect(output).toContainText("Next invoice amount: Unavailable");
});

test("owner Usage rejects cross-tenant and missing identities before displaying recorded quotas",async ({page}) => {
  const fixture=await signedIn(page);
  for (const identity of ["sample-labs",null]) {
    fixture.override=req=>req.path==="/v1/admin/tenants/sample-studio/usage" ?
      {status:200,contentType:"application/json",body:JSON.stringify({
        status:"ok",usage:{tenant_id:identity,requests_recorded:999999},
        read_audit_event_id:"a".repeat(64)})}:null;
    await page.locator("#nav-tenants").click();
    await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
      .getByRole("button",{name:"Usage",exact:true}).click();
    await expect(page.locator("#usage-details")).toContainText("Verified tenant source does not match");
    await expect(page.locator("#usage-details")).not.toContainText("999999");
  }
  expect(fixture.calls.filter(x=>x.path.endsWith("/usage")&&x.method!=="GET")).toHaveLength(0);
});

test("owner Stripe review rejects a misbound or missing account from successful provider reads",async ({page}) => {
  const fixture=await signedIn(page);
  for (const identity of ["sample-labs",null]) {
    fixture.override=req=>req.path==="/v1/admin/subscriptions/sample-studio" ?
      {status:200,contentType:"application/json",body:JSON.stringify({
        tenant_id:identity,subscription:{verified:true,status:"active",
          reconciliation:{level:"consistent",code:"states_agree"}},
        read_audit_event_id:"a".repeat(64)})}:null;
    await page.locator("#nav-tenants").click();
    await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
      .getByRole("button",{name:"Verify Stripe",exact:true}).click();
    await expect(page.locator("#billing-details")).toContainText("Stripe verification unavailable; no entitlement change");
    await expect(page.locator("#billing-details")).not.toContainText("Provider: active");
    await expect(page.locator("#status")).toContainText("Verified tenant source does not match");
  }
  expect(fixture.calls.filter(x=>x.path.startsWith("/v1/admin/subscriptions/")&&x.method!=="GET")).toHaveLength(0);
});

test("owner invoices reject a different or missing tenant despite HTTP 200 with apparent payment",async ({page}) => {
  const fixture=await signedIn(page);
  for (const identity of ["sample-labs",null]) {
    fixture.override=req=>req.path==="/v1/admin/subscriptions/sample-studio/invoices" ?
      {status:200,contentType:"application/json",body:JSON.stringify({
        tenant_id:identity,history:{verified:true,status:"verified",count:1,has_more:false,
            page_number:1,status_filter:null,next_cursor:null,
          invoices:[{status:"paid",currency:"usd",created_utc:"2026-11-20T12:00:00Z",
            amount_due_minor:999999,amount_paid_minor:999999,amount_remaining_minor:0,
            collection_method:"charge_automatically",attempted:true,attempt_count:1,
            next_payment_attempt_utc:null}]},
        read_audit_event_id:"a".repeat(64)})}:null;
    await page.locator("#nav-tenants").click();
    await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
      .getByRole("button",{name:"Invoices",exact:true}).click();
    await expect(page.locator("#invoice-details")).toContainText("Invoice verification unavailable; no billing changes");
    await expect(page.locator("#invoice-details")).not.toContainText("paid USD");
  }
  expect(fixture.calls.filter(x=>x.path.endsWith("/invoices")&&x.method!=="GET")).toHaveLength(0);
});


test("usage history distinguishes recorded zero from missing months and refuses malformed history", async ({page}) => {
  const fixture = await signedIn(page);
  const select = async () => {
    await page.locator("#nav-tenants").click();
    await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
      .getByRole("button",{name:"Usage",exact:true}).click();
  };
  await select();
  await expect(page.locator("#usage-details")).toContainText("2026-11: 1460; 2026-10: 0; 2026-09: Unknown; 2026-08: 2300");
  await expect(page.locator("#usage-details")).toContainText("current month partial; unknown is not zero");
  fixture.override = request => request.path.endsWith("/sample-studio/usage")
    ? {status:200,contentType:"application/json",body:JSON.stringify({usage:{
        tenant_id:"sample-studio",period_utc:"2026-11",requests_recorded:7,
        calendar_usage_months:Array.from({length:12},()=>({month_utc:"2026-11",requests:7}))
      }})} : null;
  await select();
  await expect(page.locator("#usage-details")).toContainText("unknown is not zero): Unavailable");
  await expect(page.locator("#usage-details")).not.toContainText("2026-10: 0");
});


test("older Cognito exact lookup cannot replace the newly selected account", async ({page}) => {
  await signedIn(page);
  await page.locator("#nav-users").click();
  let release, started, done;
  const hold = new Promise(r => release=r), ready = new Promise(r => started=r),
    delivered = new Promise(r => done=r);
  await page.route("**/v1/admin/users/lookup", async route => {
    const email = route.request().postDataJSON().email;
    if (email === "old@example.com") {started(); await hold;}
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",user:{username:email === "old@example.com" ? "old-account" : "new-account",
        email_hint:"x***@example.com",status:"CONFIRMED",enabled:true,email_verified:true},
      source:"Synthetic Cognito"})});
    if (email === "old@example.com") done();
  });
  await page.locator("#user-exact-email").fill("old@example.com");
  await page.locator("#user-inspect").click();
  await ready;
  await page.locator("#user-exact-email").fill("new@example.com");
  await page.locator("#user-inspect").click();
  await expect(page.locator("#user-inspect-result")).toContainText("new-account");
  release(); await delivered; await page.waitForTimeout(50);
  await expect(page.locator("#user-inspect-result")).toContainText("new-account");
  await expect(page.locator("#user-inspect-result")).not.toContainText("old-account");
});

test("editing a receipt selection invalidates an in-flight historical read", async ({page}) => {
  await signedIn(page,{features:{cognito_action_receipts:true}});
  await page.locator("#nav-users").click();
  let release, started, done;
  const hold = new Promise(r => release=r), ready = new Promise(r => started=r),
    delivered = new Promise(r => done=r);
  await page.route("**/v1/admin/users/status/receipt", async route => {
    started(); await hold;
    await route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({
      status:"not_found",intent_event_id:null,outcome_event_id:null,current_provider_state_verified:false})});
    done();
  });
  await page.locator("#user-action-key").fill("first_action_reference");
  await page.locator("#user-action-inspect").click();
  await ready;
  await page.locator("#user-action-key").fill("second_action_reference");
  release(); await delivered; await page.waitForTimeout(50);
  await expect(page.locator("#user-action-result")).toContainText("Action selection changed");
  await expect(page.locator("#user-action-result")).not.toContainText("No signed intent");
});

test("older audit failure cannot replace the newly verified receipt", async ({page}) => {
  const fixture = await signedIn(page);
  await page.locator("#nav-audit").click();
  let release, started, done;
  const hold = new Promise(r => release=r), ready = new Promise(r => started=r),
    delivered = new Promise(r => done=r);
  const old = "f".repeat(64);
  await page.route("**/v1/admin/audit/"+old, async route => {
    started(); await hold;
    await route.fulfill({status:503,contentType:"application/json",body:'{"detail":"old read failed"}'});
    done();
  });
  await page.locator("#audit-id").fill(old);
  await page.locator("#audit-go").click();
  await ready;
  await page.locator("#audit-id").fill(fixture.receipt);
  await page.locator("#audit-go").click();
  await expect(page.locator("#audit-result")).toContainText("HMAC integrity verified");
  release(); await delivered; await page.waitForTimeout(50);
  await expect(page.locator("#audit-result")).toContainText("HMAC integrity verified");
});


test("owner tenant directory identifies actionable saved quota risk on bounded partial pages", async ({page}) => {
  const base = {display_name:"Saved account",status:"active",demo:false,
    subscription_status:"inactive",revision:2,monthly_allowance:100,
    overage_policy:"allow_and_record",usage_month:"2026-11"};
  let rows = [
    {...base,tenant_id:"quota-watch",usage_month_count:80},
    {...base,tenant_id:"quota-near",usage_month_count:95},
    {...base,tenant_id:"quota-over",usage_month_count:101},
    {...base,tenant_id:"quota-zero",monthly_allowance:0,usage_month_count:0},
    {...base,tenant_id:"quota-missing",usage_month_count:null},
    {...base,tenant_id:"quota-old",usage_month:"2026-10",usage_month_count:100},
    {...base,tenant_id:"quota-low",usage_month_count:79}
  ];
  let stamp = "2026-11-23T12:00:00Z";
  await signedIn(page,{override:request => request.path === "/v1/admin/tenants" ?
    {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",tenants:rows,returned:rows.length,scanned:rows.length,
      next_cursor:"more",retrieved_utc:stamp})} : null});
  await page.locator("#nav-tenants").click();
  const risk = id => page.locator("#tenant-rows tr").filter({hasText:id}).locator("td").nth(6);
  await expect(risk("quota-watch")).toHaveText("Watch (80%+) · 2026-11");
  await expect(risk("quota-near")).toHaveText("Near allowance (95%+) · 2026-11");
  await expect(risk("quota-over")).toHaveText("Above allowance · 2026-11");
  await expect(risk("quota-zero")).toHaveText("At allowance · 2026-11");
  await expect(risk("quota-low")).toHaveText("Below 80% · 2026-11");
  await expect(risk("quota-missing")).toHaveText("Unavailable");
  await expect(risk("quota-old")).toHaveText("Unavailable");
  await expect(page.locator("#next")).toBeEnabled();
  await expect(page.locator("#tenant-rows tr").filter({hasText:"quota-over"}))
    .toContainText("Allow and record overage");
  stamp = "2026-11-31T12:00:00Z";
  await page.locator("#refresh").click();
  await expect(risk("quota-over")).toHaveText("Unavailable");
  stamp = "2026-11-23T12:00:00Z";
  rows = [{...base,tenant_id:"quota-over",usage_month_count:Number.MAX_SAFE_INTEGER+1}];
  await page.locator("#refresh").click();
  await expect(risk("quota-over")).toHaveText("Unavailable");
});


test("owner quota triage filters only the loaded page and preserves provider pagination", async ({page}) => {
  const base = {display_name:"Saved account",status:"active",demo:false,
    subscription_status:"inactive",revision:2,monthly_allowance:100,
    overage_policy:"allow_and_record",usage_month:"2026-11"};
  const first = [
    {...base,tenant_id:"quota-low",usage_month_count:79},
    {...base,tenant_id:"quota-watch",usage_month_count:80},
    {...base,tenant_id:"quota-near",usage_month_count:95},
    {...base,tenant_id:"quota-at",usage_month_count:100},
    {...base,tenant_id:"quota-over",usage_month_count:101},
    {...base,tenant_id:"quota-unknown",usage_month_count:null}];
  let calls = 0;
  const fixture = await signedIn(page,{override:request => {
    if (request.path !== "/v1/admin/tenants") return null;
    calls++;
    const rows = calls === 1 ? first : [{...base,tenant_id:"quota-next",usage_month_count:99}];
    return {status:200,contentType:"application/json",body:JSON.stringify({
      status:"ok",tenants:rows,returned:rows.length,scanned:rows.length,
      next_cursor:calls === 1 ? "more" : null,retrieved_utc:"2026-11-23T12:00:00Z"})};
  }});
  await page.locator("#nav-tenants").click();
  const filter = page.locator("#quota-risk-filter");
  const visible = page.locator("#tenant-rows tr:visible");
  await expect(visible).toHaveCount(6);
  await filter.selectOption("watch");
  await expect(visible).toHaveCount(4);
  await filter.selectOption("near");
  await expect(visible).toHaveCount(3);
  await filter.selectOption("at");
  await expect(visible).toHaveCount(2);
  await filter.selectOption("unknown");
  await expect(visible).toHaveCount(1);
  await expect(visible).toContainText("quota-unknown");
  expect(calls).toBe(1);
  await expect(page.locator("#next")).toBeEnabled();
  await page.locator("#next").click();
  await expect(page.locator("#tenant-rows tr")).toHaveCount(1);
  await expect(visible).toHaveCount(0);
  await expect(page.locator("#next")).toBeDisabled();
  await filter.selectOption("all");
  await expect(visible).toHaveCount(1);
  await expect(visible).toContainText("quota-next");
  expect(calls).toBe(2);
  expect(fixture.calls.filter(x=>x.path==="/v1/admin/tenants"&&x.method!=="GET")).toHaveLength(0);
});
