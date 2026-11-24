"use strict";

const {test,expect} = require("@playwright/test");
const {execFileSync} = require("node:child_process");

// No request interception: every admin HTTP response comes from the mounted
// application. Only its external identity, payment and database boundaries are
// synthetic. This does not establish deployed Cognito, AWS or Stripe behavior.
const TENANT = "usr-admin-alpha";
const ORIGIN = "http://127.0.0.1:8780";
const path = process.env.GUARDOATH_ADMIN_TEST_DB;

function persisted() {
  const program = [
    "import json,sys",
    "sys.path.insert(0,'tests')",
    "from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY",
    "from guardoath.dynamodb_store import DynamoDBTenantStore",
    "from guardoath.admin_audit import AdminAuditReader",
    "from admin_cancellation_support import verified_financial_rows",
    "db=AdminTestDynamo(sys.argv[1])",
    "store=DynamoDBTenantStore('tenant_table',db)",
    "reader=AdminAuditReader(db,'audit_table',verify_key=ADMIN_BROWSER_AUDIT_KEY)",
    "receipts=[reader.read(row['event_id']['S']) for row in db.audit_items() if 'financial_operation' not in row and not row['operation']['S'].startswith('catalog.') ]",
    "print(json.dumps({'tenants':{key:store.get(key).model_dump(mode='json') for key in ('usr-admin-alpha','usr-admin-beta')},'receipts':receipts,'financial':verified_financial_rows(db)}))",
    "db.close()",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON || "python",["-c",program,path],{encoding:"utf8"}));
}

async function callback(page,code="owner") {
  await page.addInitScript(() => sessionStorage.setItem("go_admin_pkce",
    JSON.stringify({state:"integration-state",verifier:"v".repeat(43)})));
  const exchanged = page.waitForResponse(r => r.url().endsWith("/v1/admin/auth/exchange"));
  await page.goto(`/admin/login?code=${code}&state=integration-state`);
  return exchanged;
}

async function login(page) {
  const response = await callback(page);
  expect(response.status()).toBe(200);
  const result = await response.json();
  await expect(page.locator("#app")).toBeVisible();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  return {"X-GuardOath-Admin-Session":result.access_token,Origin:ORIGIN};
}

function dialogs(page) {
  const answers = [];
  page.on("dialog",async dialog => {
    if (dialog.type() === "confirm") await dialog.accept();
    else {
      expect(answers.length,dialog.message()).toBeGreaterThan(0);
      await dialog.accept(answers.shift());
    }
  });
  return answers;
}

const row = page => page.locator("#tenant-rows tr").filter({hasText:TENANT});
const mutation = (page,path) => page.waitForResponse(r =>
  r.url().endsWith(path) && r.request().method() === "POST");

test("real owner browser reconciles, caps and suspends the authoritative tenant with durable receipts",async ({page,request}) => {
  const before = persisted().tenants;
  const headers = await login(page);
  const answers = dialogs(page);
  await page.locator("#nav-tenants").click();
  await expect(page.locator("#tenant-rows tr")).toHaveCount(2);
  answers.push("RECONCILE " + TENANT,"Verify synthetic provider subscription");
  const firstResponse = mutation(page,`/v1/admin/subscriptions/${TENANT}/reconcile`);
  await row(page).getByRole("button",{name:"Reconcile subscription",exact:true}).click();
  const first = await firstResponse;
  expect(first.status()).toBe(200);
  const firstOutcome = await first.json();
  await expect(page.locator("#billing-action-result")).toContainText("Subscription reconciled with Stripe");
  await expect(page.locator("#billing-action-result")).toContainText("Saved allowance: 10000");
  await expect(page.locator("#status")).toContainText("Subscription reconciled with Stripe");
  const receipt = await request.get(`/v1/admin/audit/${firstOutcome.audit_event_id}`,{headers});
  expect(receipt.status()).toBe(200);
  expect((await receipt.json()).audit).toMatchObject({integrity_status:"signed_verified",operation:"billing.reconcile",new_revision:8});
  const activated = persisted().tenants;
  expect(activated[TENANT]).toMatchObject({subscription_status:"active",monthly_allowance:10000,
    stripe_subscription_id:"sub_alpha",stripe_plan_monthly_allowance:10000,admin_monthly_allowance_cap:null,revision:8});
  for (const field of ["key_hashes","usage","input_rule_actions","input_attachment_csv_mode","input_check_pii","output_check_pii"])
    expect(activated[TENANT][field]).toEqual(before[TENANT][field]);
  expect(activated["usr-admin-beta"]).toEqual(before["usr-admin-beta"]);

  // Recover the actual wire request's persisted result, even from a separate
  // client. It cannot run a second transaction or return today's later state.
  const replay = await request.post(`/v1/admin/subscriptions/${TENANT}/reconcile`,{
    headers,data:first.request().postDataJSON()});
  expect(replay.status()).toBe(200);
  expect(await replay.json()).toMatchObject({replayed:true,revision:8,audit_event_id:firstOutcome.audit_event_id});
  expect(persisted().tenants[TENANT]).toEqual(activated[TENANT]);

  await page.locator("#nav-tenants").click();
  answers.push("4000",TENANT,"Set synthetic approved request budget");
  await row(page).getByRole("button",{name:"Cap usage",exact:true}).click();
  await expect(page.locator("#status")).toContainText("Request cap saved");
  expect(persisted().tenants[TENANT]).toMatchObject({monthly_allowance:4000,admin_monthly_allowance_cap:4000,revision:9});
  answers.push("RECONCILE " + TENANT,"Recheck without removing saved request cap");
  await row(page).getByRole("button",{name:"Reconcile subscription",exact:true}).click();
  await expect(page.locator("#billing-action-result")).toContainText("Preserved request cap: 4000");
  await expect(page.locator("#status")).toContainText("Preserved request cap: 4000");
  expect(persisted().tenants[TENANT]).toMatchObject({monthly_allowance:4000,admin_monthly_allowance_cap:4000,revision:10});

  await page.locator("#nav-tenants").click();
  answers.push(TENANT,"Suspend synthetic account for review");
  await row(page).getByRole("button",{name:"Suspend",exact:true}).click();
  await expect(page.locator("#status")).toContainText("Tenant access updated");
  answers.push("RECONCILE " + TENANT,"Verify provider without enabling suspended tenant");
  await row(page).getByRole("button",{name:"Reconcile subscription",exact:true}).click();
  await expect(page.locator("#billing-action-result")).toContainText("Tenant remains suspended");
  await expect(page.locator("#status")).toContainText("Tenant remains suspended");
  const saved = persisted();
  expect(saved.tenants[TENANT]).toMatchObject({status:"disabled",monthly_allowance:4000,revision:12});
  expect(saved.tenants["usr-admin-beta"]).toEqual(before["usr-admin-beta"]);
  expect(saved.receipts.filter(r=>r.operation === "billing.reconcile")).toHaveLength(3);
  expect(saved.receipts.every(r=>r.integrity_status === "signed_verified")).toBe(true);
  expect(answers).toEqual([]);
  const token = headers["X-GuardOath-Admin-Session"];
  expect(await page.evaluate(()=>Object.values(sessionStorage).join(" "))).not.toContain(token);
  expect(await page.evaluate(()=>Object.values(localStorage).join(" "))).not.toContain(token);
});

test("real owner user management verifies Cognito and signed intent/outcome without changing billing",async ({page}) => {
  const before = persisted().tenants;
  await login(page);
  const answers = dialogs(page);
  await page.locator("#nav-users").click();
  for (const action of ["disable","enable"]) {
    await page.locator("#user-change-email").fill("customer@example.invalid");
    await page.locator("#user-change-action").selectOption(action);
    answers.push(action.toUpperCase()+" customer@example.invalid","Verify synthetic sign-in access management");
    await page.locator("#user-change").click();
    await expect(page.locator("#user-change-result")).toContainText("Cognito sign-in access changed and verified");
    await page.locator("#user-action-inspect").click();
    await expect(page.locator("#user-action-result")).toContainText("current enabled state matches historical snapshot");
    await expect(page.locator("#user-action-result")).toContainText("current Cognito enabled: " + (action === "enable"));
  }
  const saved = persisted();
  expect(saved.tenants).toEqual(before);
  expect(saved.receipts.filter(r=>r.operation === "cognito.user.status.intent")).toHaveLength(2);
  expect(saved.receipts.filter(r=>r.operation === "cognito.user.status.outcome")).toHaveLength(2);
  expect(answers).toEqual([]);
});

test("real owner catalog review persists signed drafts and reversible plans without publishing",async ({page,request}) => {
  const headers = await login(page);
  const answers = dialogs(page);
  const source = await (await request.get("/v1/admin/catalog",{headers})).json();
  const before = persisted().tenants;
  await page.locator("#nav-catalog").click();
  const entry = page.locator("#categories li").filter({has:page.locator("strong").filter({hasText:/^pii\.email_address$/})});
  await entry.evaluate(node=>{let parent=node.parentElement;while(parent){if(parent.tagName === "DETAILS")parent.open=true;parent=parent.parentElement;}});
  await entry.getByRole("button",{name:"Preview description",exact:true}).click();
  await page.locator("#proposal-title").fill("Email identifier review example");
  await page.locator("#proposal-go").click();
  await expect(page.locator("#proposal-save")).toBeEnabled();
  answers.push("Review synthetic catalog description change");
  await page.locator("#proposal-save").click();
  await expect(page.locator("#proposal-result")).toContainText("Signed draft saved");
  await page.locator("#draft-inspect").click();
  await expect(page.locator("#draft-review")).toBeEnabled();
  const draftId = await page.locator("#draft-id").inputValue();
  answers.push("REVIEW " + draftId.slice(-8),"Approve synthetic metadata review evidence");
  await page.locator("#draft-review").click();
  await expect(page.locator("#draft-result")).toContainText("Immutable signed review verified");
  await page.locator("#review-manifest").click();
  await expect(page.locator("#manifest-export")).toBeEnabled();
  await expect(page.locator("#review-manifest-result")).toContainText("NOT published");
  const after = await (await request.get("/v1/admin/catalog",{headers})).json();
  expect(after.registry_version).toBe(source.registry_version);
  expect(after.categories).toEqual(source.categories);
  expect(persisted().tenants).toEqual(before);
  expect(answers).toEqual([]);
});

for (const code of ["customer","no-mfa","stale-mfa","forged"]) {
  test(`actual signed-token and owner verification denies ${code} login`,async ({page,request}) => {
    const before = persisted().tenants;
    const response = await callback(page,code);
    expect(response.status()).toBe(403);
    await expect(page.locator("#app")).toBeHidden();
    await expect(page.locator("#tenant-rows tr")).toHaveCount(0);
    expect((await request.get("/v1/admin/tenants")).status()).toBe(401);
    expect((await request.get("/v1/admin/tenants",{headers:{"X-GuardOath-Key":"synthetic-customer-key"}})).status()).toBe(401);
    expect(persisted().tenants).toEqual(before);
  });
}

test("real owner schedules one provider cancellation and reopens linked history without tenant changes",async ({page,request}) => {
  const headers = await login(page);
  let before = persisted().tenants;
  if (!before[TENANT].stripe_subscription_id) {
    const bound = await request.post("/v1/admin/subscriptions/"+TENANT+"/reconcile",{headers,data:{
      expected_revision:before[TENANT].revision,confirmation:"RECONCILE "+TENANT,
      reason:"Bind synthetic account before cancellation acceptance",idempotency_key:"cancel-setup-123456789"}});
    expect(bound.status()).toBe(200);
    await page.locator("#refresh").click();
    await expect(page.locator("#status")).toContainText("Administration loaded");
    before = persisted().tenants;
  }
  await page.locator("#nav-tenants").click();
  await row(page).getByRole("button",{name:"Review cancellation",exact:true}).click();
  await expect(page.locator("#cancellation-review")).toContainText("Scheduled end UTC");
  await page.locator("#cancellation-reason").fill("Owner requested synthetic period end cancellation");
  await page.locator("#cancellation-confirm").fill("SCHEDULE CANCELLATION wrong-tenant");
  await expect(page.locator("#cancellation-schedule")).toBeDisabled();
  await page.locator("#cancellation-confirm").fill("SCHEDULE CANCELLATION "+TENANT);
  const changed = mutation(page,"/v1/admin/subscriptions/"+TENANT+"/cancel-at-period-end");
  await page.locator("#cancellation-schedule").click();
  const response = await changed;
  expect(response.status()).toBe(200);
  const action = (await response.json()).cancellation;
  expect(action).toMatchObject({status:"verified",tenant_mutated:false,provider_state_refreshed:false});
  await expect(page.locator("#cancellation-result")).toContainText("Verified historical schedule");
  const reference = await page.locator("#cancellation-reference").inputValue();
  expect(reference).toBe(action.action_reference);
  await page.locator("#cancellation-receipt").click();
  await expect(page.locator("#cancellation-receipt-result")).toContainText("Verified historical schedule");
  const reopened = persisted();
  expect(reopened.tenants).toEqual(before);
  expect(reopened.financial.map(value=>value.kind).sort()).toEqual(["control","intent","outcome"]);
  const intent = reopened.financial.find(value=>value.kind==="intent");
  const outcome = reopened.financial.find(value=>value.kind==="outcome");
  const control = reopened.financial.find(value=>value.kind==="control");
  expect(control.state).toBe("VERIFIED");
  expect(outcome).toMatchObject({action_id:intent.action_id,status:"verified",
    update_hash:intent.provider_hash,verification_hash:intent.provider_hash});
  expect(control.intent_hash).toBe(outcome.intent_hash);
  const replay = await request.post("/v1/admin/subscriptions/"+TENANT+"/cancel-at-period-end",{
    headers,data:response.request().postDataJSON()});
  expect(replay.status()).toBe(200);
  expect((await replay.json()).cancellation).toEqual(action);
  const fs = require("node:fs"), nodePath = require("node:path");
  const parsed = nodePath.parse(path);
  const evidence = fs.readFileSync(nodePath.join(parsed.dir,parsed.name+".cancellation-requests.jsonl"),"utf8")
    .trim().split("\n").map(JSON.parse);
  const posts = evidence.filter(value=>value.method==="POST");
  expect(posts).toHaveLength(1);
  expect(posts[0]).toMatchObject({url:"https://api.stripe.com/v1/subscriptions/sub_alpha",
    body:"cancel_at_period_end=true"});
  expect(posts[0].idempotency_key).toMatch(/^guardoath-sc1-[a-f0-9]{64}$/);
  expect(persisted().tenants).toEqual(before);
});
