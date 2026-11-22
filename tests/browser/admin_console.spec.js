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
  await expect(page.locator("#metrics .metric strong")).toHaveText(["Unavailable","Unavailable","Unavailable","Unavailable"]);
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

test("owner Billing shows verified collection attempt metadata without claiming failed payment", async ({page}) => {
  const fixture = await signedIn(page, {override: request =>
    /^\/v1\/admin\/subscriptions\/[^/]+\/invoices$/.test(request.path)
      ? {status:200, contentType:"application/json", body:JSON.stringify({
          history:{verified:true,status:"verified",count:1,has_more:false,
            read_at_utc:"2026-11-21T12:00:00Z",invoices:[{
              status:"open",currency:"usd",created_utc:"2026-11-20T12:00:00Z",
              amount_due_minor:2500,amount_paid_minor:0,amount_remaining_minor:2500,
              collection_method:"charge_automatically",attempted:true,attempt_count:2,
              next_payment_attempt_utc:"2026-11-23T12:00:00Z"}]},
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
  await expect(output).toContainText("not proof of payment failure");
  expect(fixture.calls.some(call => call.path.endsWith("/invoices") &&
    call.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
});

test("verified inactive Stripe disagreement appears in owner Billing without inventing paid access", async ({page}) => {
  const fixture = await signedIn(page,{override: request =>
    /^\/v1\/admin\/subscriptions\/[^/]+$/.test(request.path)
      ? {status:200,contentType:"application/json",body:JSON.stringify({
          subscription:{verified:true,status:"canceled",verified_at:"2026-11-21T12:00:00Z",
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
          check_id:"pii",rule_id:"pii.email",changed_fields:["title"],published:false,
          ready_to_publish:false,tenant_policies_changed:false,detector_behavior_changed:false,
          source_current:true,acceptance_tests_run:false,
          release_kind:"review_only_projection_not_executable_source"});
      return null;
    }
  });
  await page.locator("#nav-catalog").click();
  await expect(page.locator("#review-manifest")).toBeDisabled();
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
  expect(fixture.calls.some(c => c.path.endsWith("/manifest") &&
    c.headers["x-guardoath-admin-session"] === fixture.token)).toBe(true);
  await expect(page.locator("#review-manifest")).toBeDisabled();
  await page.locator("#logout").click();
  await expect(page.locator("#review-manifest-result")).toBeEmpty();
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
