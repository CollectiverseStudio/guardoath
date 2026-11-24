"use strict";

const {test,expect} = require("@playwright/test");
const {execFileSync} = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const TENANT = "usr-admin-alpha";
const ORIGIN = "http://127.0.0.1:8781";

function invoiceReads() {
  const dbPath = path.parse(process.env.GUARDOATH_ADMIN_TEST_DB);
  const evidence = path.join(dbPath.dir,dbPath.name+".invoice-reads.jsonl");
  return fs.existsSync(evidence) ? fs.readFileSync(evidence,"utf8").trim().split("\n").filter(Boolean).map(JSON.parse) : [];
}

function saved() {
  const program = [
    "import json,sys",
    "sys.path.insert(0,'tests')",
    "from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY",
    "from guardoath.admin_audit import AdminAuditReader",
    "db=AdminTestDynamo(sys.argv[1])",
    "reader=AdminAuditReader(db,'audit_table',verify_key=ADMIN_BROWSER_AUDIT_KEY)",
    "tenants={}",
    "for key in ('usr-admin-alpha','usr-admin-beta'):",
    " item=db.get_item(TableName='tenant_table',Key={'tenant_id':{'S':key}},ConsistentRead=True)['Item']",
    " tenants[key]={'record':json.loads(item['record']['S']),'revision':int(item['revision']['N']),'usage':item['usage']}",
    "receipts=[reader.read(row['event_id']['S']) for row in db.audit_items()]",
    "print(json.dumps({'tenants':tenants,'receipts':receipts}))",
    "db.close()",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON || "python",
    ["-c",program,process.env.GUARDOATH_ADMIN_TEST_DB],{encoding:"utf8"}));
}

async function login(page) {
  await page.addInitScript(()=>sessionStorage.setItem("go_admin_pkce",
    JSON.stringify({state:"baseline-state",verifier:"v".repeat(43)})));
  const exchanged = page.waitForResponse(r=>r.url().endsWith("/v1/admin/auth/exchange"));
  await page.goto("/admin/login?code=owner&state=baseline-state");
  const response = await exchanged;
  expect(response.status()).toBe(200);
  const result = await response.json();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  return {"X-GuardOath-Admin-Session":result.access_token,Origin:ORIGIN};
}

test("baseline owner browser enforces request caps without introducing future state",async ({page,request})=>{
  const before = saved().tenants;
  const headers = await login(page);
  const features = await request.get("/v1/admin/features",{headers});
  expect(features.status()).toBe(200);
  expect(await features.json()).toMatchObject({billing_reconcile:false,billing_verify:true,
    billing_downgrade:true,cognito_user_status_changes:true,monthly_cost_caps:true});
  await page.locator("#nav-tenants").click();
  const row = page.locator("#tenant-rows tr").filter({hasText:TENANT});
  await expect(row).toHaveCount(1);
  await expect(page.getByRole("button",{name:"Reconcile subscription",exact:true})).toHaveCount(0);

  const answers = ["4000",TENANT,"Verify legacy-compatible request safeguard"];
  page.on("dialog",async dialog=>{
    if (dialog.type()==="confirm") await dialog.accept();
    else { expect(answers.length).toBeGreaterThan(0); await dialog.accept(answers.shift()); }
  });
  await row.getByRole("button",{name:"Cap usage",exact:true}).click();
  await expect(page.locator("#status")).toContainText("Request cap saved");
  const read = await request.get(`/v1/admin/subscriptions/${TENANT}`,{headers});
  expect(read.status()).toBe(200);
  expect(await read.json()).toMatchObject({tenant_id:TENANT,subscription:{verified:true,status:"active"}});
  const denied = await request.post(`/v1/admin/subscriptions/${TENANT}/reconcile`,{headers,data:{
    expected_revision:8,confirmation:"RECONCILE "+TENANT,reason:"Direct request must not enable future writer",
    idempotency_key:"baseline-direct-attempt-123"}});
  expect(denied.status()).toBe(404);

  const after = saved();
  expect(after.tenants[TENANT]).toMatchObject({revision:8,record:{monthly_allowance:4000,overage:"block"}});
  expect(after.tenants[TENANT].record).not.toHaveProperty("admin_monthly_allowance_cap");
  expect(after.tenants[TENANT].record).not.toHaveProperty("stripe_plan_monthly_allowance");
  for (const field of ["key_hashes","input_rule_actions","output_rule_actions","subscription_status","stripe_subscription_id"])
    expect(after.tenants[TENANT].record[field]).toEqual(before[TENANT].record[field]);
  expect(after.tenants[TENANT].usage).toEqual(before[TENANT].usage);
  expect(after.tenants["usr-admin-beta"]).toEqual(before["usr-admin-beta"]);
  expect(after.receipts.some(receipt=>receipt.operation==="tenant.allowance_cap")).toBe(true);
  expect(after.receipts.every(receipt=>receipt.operation!=="billing.reconcile" && receipt.integrity_status==="signed_verified")).toBe(true);
  expect(answers).toEqual([]);
});

test("baseline owner navigation remains usable at a mobile viewport",async ({page})=>{
  await page.setViewportSize({width:390,height:844});
  await login(page);
  for (const section of ["tenants","users","billing","catalog"]) {
    await page.locator("#nav-"+section).click();
    await expect(page.locator("#section-"+section)).toBeVisible();
  }
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

test("baseline explains ambiguous zero-cap refusal and owner can suspend the unbound account",async ({page})=>{
  const target = "usr-admin-beta";
  const before = saved().tenants;
  await login(page);
  await page.locator("#nav-tenants").click();
  const row = page.locator("#tenant-rows tr").filter({hasText:target});
  const answers = ["0",target,"Stop synthetic unbound account usage"];
  page.on("dialog",async dialog=>{
    if (dialog.type()==="confirm") await dialog.accept();
    else { expect(answers.length).toBeGreaterThan(0); await dialog.accept(answers.shift()); }
  });
  const refusal = page.waitForResponse(r=>r.url().endsWith(`/v1/admin/tenants/${target}/allowance-cap`));
  await row.getByRole("button",{name:"Cap usage",exact:true}).click();
  expect((await refusal).status()).toBe(409);
  await expect(page.locator("#status")).toContainText("Use Suspend to block access");
  expect(saved().tenants).toEqual(before);
  answers.push(target,"Suspend synthetic account after quota refusal");
  await row.getByRole("button",{name:"Suspend",exact:true}).click();
  await expect(page.locator("#status")).toContainText("Tenant access updated");
  const after = saved();
  expect(after.tenants[target]).toMatchObject({revision:8,record:{status:"disabled",monthly_allowance:10000}});
  expect(after.tenants[target].record).not.toHaveProperty("admin_monthly_allowance_cap");
  expect(after.tenants[TENANT]).toEqual(before[TENANT]);
  expect(after.receipts.some(receipt=>receipt.tenant_id===target && receipt.operation==="tenant.status" &&
    receipt.integrity_status==="signed_verified")).toBe(true);
  expect(answers).toEqual([]);
});

test("owner inspects actual published catalog schemas and examples with signed persistent reads",async ({page,request})=>{
  const before = saved().tenants;
  const headers = await login(page);
  await page.locator("#nav-catalog").click();
  const category = page.locator("#categories > details").filter({has:page.locator("summary",{hasText:"Privacy and personal data"})});
  await category.locator(":scope > summary").click();
  const check = category.locator(":scope > details").filter({has:page.getByText("Personal identifiers",{exact:true})});
  await check.locator(":scope > summary").click();
  const observed = page.waitForResponse(response=>new URL(response.url()).pathname===
    "/v1/admin/catalog/categories/privacy/checks/pii");
  await check.getByRole("button",{name:"Inspect check",exact:true}).click();
  const response = await observed;
  expect(response.status()).toBe(200);
  const report = await response.json();
  expect(report).toMatchObject({read_only:true,category:{category_id:"privacy"},check:{check_id:"pii"},rule:null,
    test_evidence:{ci_verified_for_source:false,live_deployment_verified:null}});
  await expect(page.locator("#catalog-inspector-status")).toContainText("Published pii · registry "+report.registry_version);
  await expect(page.locator("#catalog-inspector-content")).toContainText("privacy_min_score");
  await expect(page.locator("#catalog-inspector-content")).toContainText("tests/test_pii.py");
  await expect(page.locator("#catalog-inspector-content")).toContainText("not current CI or live deployment evidence");
  const rule = check.locator("li").filter({has:page.getByText("pii.email_address",{exact:true})});
  await rule.getByRole("button",{name:"Inspect rule",exact:true}).click();
  await expect(page.locator("#catalog-inspector-status")).toContainText("Published pii.email_address");
  await expect(page.locator("#catalog-inspector-content")).toContainText("jane@example.com");
  const receipt = await request.get("/v1/admin/audit/"+report.read_audit_event_id,{headers});
  expect(receipt.status()).toBe(200);
  expect((await receipt.json()).audit).toMatchObject({integrity_status:"signed_verified",operation:"admin.read",
    change:{field:"protected_read",resource:"catalog.registry"}});
  expect(saved().tenants).toEqual(before);
  await page.locator("#logout").click();
  await expect(page.locator("#catalog-inspector-content")).toBeEmpty();
});

test("owner browses older and filtered invoices through actual authenticated API and signed audit",async ({page,request})=>{
  const before = saved().tenants;
  const providerBefore = invoiceReads().length;
  const headers = await login(page);
  await page.locator("#nav-tenants").click();
  const firstRead = page.waitForResponse(response=>new URL(response.url()).pathname===
    `/v1/admin/subscriptions/${TENANT}/invoices`);
  await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Invoices",exact:true}).click();
  const firstResponse = await firstRead;
  expect(firstResponse.status()).toBe(200);
  const first = await firstResponse.json();
  expect(first.history).toMatchObject({verified:true,page_number:1,status_filter:null,count:5,has_more:true});
  expect(first.history.next_cursor).toMatch(/^ic1\.[A-Za-z0-9_-]+$/);
  expect(JSON.stringify(first)).not.toMatch(/in_alpha|cus_alpha|sub_alpha|private-invoice/);
  await expect(page.locator("#invoice-details")).toContainText("paid USD due 700");
  const nextRead = page.waitForResponse(response=>new URL(response.url()).pathname===
    `/v1/admin/subscriptions/${TENANT}/invoices`&&new URL(response.url()).searchParams.has("cursor"));
  await page.locator("#invoice-next").click();
  const second = await (await nextRead).json();
  expect(second.history).toMatchObject({page_number:2,count:3,has_more:false,next_cursor:null,
    payment_review:{complete_history:false,scope:"verified_selected_page_only"}});
  await expect(page.locator("#invoice-details")).toContainText("open USD due 706");
  await expect(page.locator("#invoice-details")).toContainText("uncollectible EUR due 707");
  await expect(page.locator("#invoice-next")).toBeDisabled();
  await page.locator("#invoice-status-filter").selectOption("uncollectible");
  await expect(page.locator("#invoice-page-status")).toContainText("Page 1 · uncollectible");
  await expect(page.locator("#invoice-details")).toContainText("uncollectible EUR due 707");
  await expect(page.locator("#invoice-details")).not.toContainText("open USD due 706");
  const reads = invoiceReads().slice(providerBefore);
  expect(reads).toEqual([
    {tenant_id:TENANT,starting_after:null,status:null},
    {tenant_id:TENANT,starting_after:"in_alpha_4",status:null},
    {tenant_id:TENANT,starting_after:null,status:"uncollectible"},
  ]);
  const mismatched = await request.get(`/v1/admin/subscriptions/${TENANT}/invoices`,{headers,
    params:{cursor:first.history.next_cursor,status:"open"}});
  expect(mismatched.status()).toBe(400);
  expect(invoiceReads().slice(providerBefore)).toEqual(reads);
  for (const result of [first,second]) {
    const receipt = await request.get("/v1/admin/audit/"+result.read_audit_event_id,{headers});
    expect(receipt.status()).toBe(200);
    expect((await receipt.json()).audit).toMatchObject({tenant_id:TENANT,
      operation:"admin.read",integrity_status:"signed_verified",
      change:{field:"protected_read",resource:"stripe.invoices"}});
  }
  expect(saved().tenants).toEqual(before);
  await page.locator("#logout").click();
  await expect(page.locator("#invoice-page-status")).toBeEmpty();
  await expect(page.locator("#invoice-next")).toBeDisabled();
  await expect(page.locator("#invoice-details")).not.toContainText("707");
});

for (const code of ["customer","no-mfa","stale-mfa","forged"]) {
  test(`actual invoice browser denies ${code} before provider reads`,async ({page,request})=>{
    const before = invoiceReads();
    await page.addInitScript(()=>sessionStorage.setItem("go_admin_pkce",
      JSON.stringify({state:"invoice-denial",verifier:"v".repeat(43)})));
    const exchanged = page.waitForResponse(r=>r.url().endsWith("/v1/admin/auth/exchange"));
    await page.goto(`/admin/login?code=${code}&state=invoice-denial`);
    expect((await exchanged).status()).toBe(403);
    await expect(page.locator("#app")).toBeHidden();
    expect((await request.get(`/v1/admin/subscriptions/${TENANT}/invoices`)).status()).toBe(401);
    expect((await request.get(`/v1/admin/subscriptions/${TENANT}/invoices`,{
      headers:{"X-GuardOath-Key":"synthetic-customer-key"}})).status()).toBe(401);
    expect(invoiceReads()).toEqual(before);
  });
}
