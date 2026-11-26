"use strict";

const {test,expect}=require("@playwright/test");
const {execFileSync}=require("node:child_process");
const fs=require("node:fs");
const path=require("node:path");

// No HTTP interception. Owner JWT/MFA, invoice references, refund provider,
// durable claims and signed history use the mounted production implementations.
// Only the external identity, Stripe and DynamoDB boundaries are synthetic.
const TENANT="usr-admin-alpha",ORIGIN="http://127.0.0.1:8782";
const PREFIX="/v1/admin/subscriptions/"+TENANT;
const database=process.env.GUARDOATH_ADMIN_TEST_DB;
const parsed=path.parse(database);
const evidence=suffix=>path.join(parsed.dir,parsed.name+suffix);
const providerRequests=()=>fs.existsSync(evidence(".refund-requests.jsonl")) ?
  fs.readFileSync(evidence(".refund-requests.jsonl"),"utf8").trim().split("\n").filter(Boolean).map(JSON.parse) : [];

function persisted() {
  const program=[
    "import json,sys",
    "sys.path.insert(0,'tests')",
    "from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY",
    "from admin_refund_support import verified_refund_rows",
    "from guardoath.admin_audit import AdminAuditReader",
    "db=AdminTestDynamo(sys.argv[1])",
    "reader=AdminAuditReader(db,'audit_table',verify_key=ADMIN_BROWSER_AUDIT_KEY)",
    "tenants={key:db.get_item(TableName='tenant_table',Key={'tenant_id':{'S':key}},ConsistentRead=True)['Item'] for key in ('usr-admin-alpha','usr-admin-beta')}",
    "receipts=[reader.read(row['event_id']['S']) for row in db.audit_items() if 'financial_operation' not in row and 'catalog_publication' not in row and not row['operation']['S'].startswith('catalog.') ]",
    "print(json.dumps({'tenants':tenants,'receipts':receipts,'refunds':verified_refund_rows(db)}))",
    "db.close()",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON || "python",["-c",program,database],{encoding:"utf8"}));
}

async function login(page) {
  await page.addInitScript(()=>sessionStorage.setItem("go_admin_pkce",
    JSON.stringify({state:"refund-mounted-state",verifier:"v".repeat(43)})));
  const returned=page.waitForResponse(r=>r.url().endsWith("/v1/admin/auth/exchange"));
  await page.goto("/admin/login?code=owner&state=refund-mounted-state");
  const response=await returned;
  expect(response.status()).toBe(200);
  const result=await response.json();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  return {"X-GuardOath-Admin-Session":result.access_token,Origin:ORIGIN};
}

test("real owner invoice review submits once, refreshes pending status and recovers signed history",async({page,request})=>{
  const headers=await login(page);
  const features=await request.get("/v1/admin/features",{headers});
  expect(features.status()).toBe(200);
  expect(await features.json()).toMatchObject({billing_refund_preview:true,billing_refund_submit:true,
    billing_refund_receipts:true,billing_refund_refresh:true,
    billing_configuration:{refund_provider:true,refund_writer_enabled:true}});
  const reconciled=await request.post(PREFIX+"/reconcile",{headers,data:{expected_revision:7,
    confirmation:"RECONCILE "+TENANT,reason:"Establish the mounted synthetic subscription",
    idempotency_key:"browser-refund-reconcile-123456789"}});
  expect(reconciled.status()).toBe(200);
  const before=persisted().tenants;
  expect(before[TENANT].revision.N).toBe("8");
  fs.writeFileSync(evidence(".refund-status"),"pending");
  await page.locator("#nav-tenants").click();
  const row=page.locator("#tenant-rows tr").filter({hasText:TENANT});
  // The visible tenant row predates reconciliation; the server invoice result
  // supplies the authoritative revision used by the refund preview and submit.
  await row.getByRole("button",{name:"Invoices",exact:true}).click();
  await expect(page.locator("#invoice-details")).toContainText("Direct Stripe");
  await expect(page.locator("#invoice-refund-actions button")).toHaveCount(5);
  await page.locator("#invoice-refund-actions button").first().click();
  await expect(page.locator("#refund-review")).toContainText("Full refund USD 7.00 (700 minor units)");
  await expect(page.locator("#refund-review")).toContainText("Test mode");
  expect(providerRequests().filter(r=>r.method==="POST")).toHaveLength(0);
  const confirmation="REFUND "+TENANT+" 700 USD MINOR UNITS";
  await page.locator("#refund-reason").fill("Refund the reviewed synthetic paid invoice");
  await page.locator("#refund-confirm").fill("REFUND "+TENANT);
  await expect(page.locator("#refund-submit")).toBeDisabled();
  await page.locator("#refund-confirm").fill(confirmation);
  const submitted=page.waitForResponse(r=>r.url().endsWith(PREFIX+"/refund") && r.request().method()==="POST");
  await page.locator("#refund-submit").click();
  const response=await submitted;
  expect(response.status()).toBe(200);
  const body=response.request().postDataJSON();
  expect(Object.keys(body).sort()).toEqual(["confirmation","expected_revision","idempotency_key","preview_reference","reason"]);
  expect(body.expected_revision).toBe(8);
  expect(body.preview_reference).toMatch(/^rf1\./);
  await expect(page.locator("#refund-result")).toContainText("Refund pending at Stripe");
  const reference=await page.locator("#refund-reference").inputValue();
  expect(reference).toBe(body.idempotency_key);
  await expect(page.locator("#refund-submit")).toBeDisabled();
  for(const button of await page.locator("#invoice-refund-actions button").all()) await expect(button).toBeDisabled();
  const pending=persisted();
  expect(pending.tenants).toEqual(before);
  expect(pending.refunds).toHaveLength(3);
  expect(pending.refunds.find(row=>row.kind==="outcome").status).toBe("pending");
  let calls=providerRequests();
  expect(calls.filter(r=>r.method==="POST")).toHaveLength(1);
  const count=calls.length;
  await page.locator("#refund-receipt").click();
  await expect(page.locator("#refund-receipt-result")).toContainText("Recovered signed history");
  await expect(page.locator("#refund-receipt-result")).toContainText("Refund pending at Stripe");
  expect(providerRequests()).toHaveLength(count);
  fs.writeFileSync(evidence(".refund-status"),"succeeded");
  await page.locator("#refund-refresh").click();
  await expect(page.locator("#refund-receipt-result")).toContainText("Provider status refreshed");
  await expect(page.locator("#refund-receipt-result")).toContainText("Verified refund");
  calls=providerRequests();
  expect(calls.slice(count).every(r=>r.method==="GET")).toBe(true);
  expect(calls.filter(r=>r.method==="POST")).toHaveLength(1);
  const final=persisted();
  expect(final.tenants).toEqual(before);
  expect(final.refunds).toHaveLength(4);
  expect(final.refunds.find(row=>row.kind==="observation")).toMatchObject({status:"verified",sequence:2});
  expect(final.receipts.every(row=>row.integrity_status==="signed_verified")).toBe(true);
  const replay=await request.post(PREFIX+"/refund",{headers,data:body});
  expect(replay.status()).toBe(200);
  expect((await replay.json()).refund.status).toBe("verified");
  expect(providerRequests()).toHaveLength(calls.length);
  const competing=await request.post(PREFIX+"/refund",{headers,data:{...body,idempotency_key:"competing-browser-refund-123456789"}});
  expect(competing.status()).toBe(409);
  expect(providerRequests().filter(r=>r.method==="POST")).toHaveLength(1);
  await page.locator("#logout").click();
  await login(page);
  await page.locator("#nav-billing").click();
  await page.locator("#refund-receipt-tenant").fill(TENANT);
  await page.locator("#refund-receipt-reference").fill(reference);
  const reopenedCalls=providerRequests().length;
  await page.locator("#refund-receipt").click();
  await expect(page.locator("#refund-receipt-result")).toContainText("Recovered signed history");
  await expect(page.locator("#refund-receipt-result")).toContainText("Verified refund");
  await expect(page.locator("#refund-receipt-result")).toContainText("USD 7.00 (700 minor units)");
  expect(providerRequests()).toHaveLength(reopenedCalls);
  expect(persisted().tenants).toEqual(before);
  await expect(page.locator("#section-billing")).not.toContainText(/cus_alpha|sub_alpha|in_alpha_0|ch_refund|re_refund/);
});
