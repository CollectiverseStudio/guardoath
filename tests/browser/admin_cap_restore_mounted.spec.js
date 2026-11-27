"use strict";

const {test,expect}=require("@playwright/test");
const {execFileSync}=require("node:child_process");
const fs=require("node:fs");
const path=require("node:path");

// No HTTP interception. The actual owner verifier, revision-bound review,
// atomic tenant/audit writer and independent signed reader are mounted.
// Identity, direct provider reads and the persistent database boundary are
// synthetic. The mounted app and independent reader use release defaults.
const TENANT="usr-admin-alpha",ORIGIN="http://127.0.0.1:8783";
const TENANT_PATH="/v1/admin/tenants/"+TENANT,PREFIX=TENANT_PATH+"/allowance-cap/restore";
const database=process.env.GUARDOATH_ADMIN_TEST_DB;
const parsed=path.parse(database);
const evidence=suffix=>path.join(parsed.dir,parsed.name+suffix);
const entries=suffix=>fs.existsSync(evidence(suffix)) ?
  fs.readFileSync(evidence(suffix),"utf8").trim().split("\n").filter(Boolean).map(JSON.parse) : [];
const providerReads=()=>entries(".cap-restore-reads.jsonl");

function persisted() {
  const program=[
    "import json,sys",
    "sys.path.insert(0,'tests')",
    "from admin_test_support import AdminTestDynamo, ADMIN_BROWSER_AUDIT_KEY",
    "from guardoath.admin_audit import AdminAuditReader",
    "from guardoath.state_contracts import new_cap_restoration_state_enabled",
    "db=AdminTestDynamo(sys.argv[1])",
    "reader=AdminAuditReader(db,'audit_table',verify_key=ADMIN_BROWSER_AUDIT_KEY)",
    "tenants={key:db.get_item(TableName='tenant_table',Key={'tenant_id':{'S':key}},ConsistentRead=True)['Item'] for key in ('usr-admin-alpha','usr-admin-beta')}",
    "receipts=[reader.read(row['event_id']['S']) for row in db.audit_items() if 'financial_operation' not in row and 'catalog_publication' not in row and not row['operation']['S'].startswith('catalog.') ]",
    "print(json.dumps({'tenants':tenants,'receipts':receipts,'source_restore_writer_enabled':new_cap_restoration_state_enabled()}))",
    "db.close()",
  ].join("\n");
  return JSON.parse(execFileSync(process.env.PYTHON || "python",["-c",program,database],{encoding:"utf8"}));
}

async function login(page) {
  await page.addInitScript(()=>sessionStorage.setItem("go_admin_pkce",
    JSON.stringify({state:"cap-restore-mounted-state",verifier:"v".repeat(43)})));
  const returned=page.waitForResponse(r=>r.url().endsWith("/v1/admin/auth/exchange"));
  await page.goto("/admin/login?code=owner&state=cap-restore-mounted-state");
  const response=await returned;
  expect(response.status()).toBe(200);
  const result=await response.json();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  return {"X-GuardOath-Admin-Session":result.access_token,Origin:ORIGIN};
}

test("source-enabled owner restoration removes the saved cap once, preserves suspension and recovers signed history",async({page,request})=>{
  const headers=await login(page);
  const flags=await request.get("/v1/admin/features",{headers});
  expect(flags.status()).toBe(200);
  expect(await flags.json()).toMatchObject({billing_cap_restore_preview:true,billing_cap_restore_submit:true,
    billing_cap_restore_receipts:true,billing_configuration:{cap_restore_writer_enabled:true}});
  const reconciled=await request.post("/v1/admin/subscriptions/"+TENANT+"/reconcile",{headers,data:{
    expected_revision:7,confirmation:"RECONCILE "+TENANT,
    reason:"Establish the mounted synthetic subscription",idempotency_key:"browser-restore-reconcile-123456789"}});
  expect(reconciled.status()).toBe(200);
  const capped=await request.post(TENANT_PATH+"/allowance-cap",{headers,data:{expected_revision:8,new_limit:4000,
    confirmation:TENANT,reason:"Keep the saved local synthetic request cap",idempotency_key:"browser-restore-cap-123456789"}});
  expect(capped.status()).toBe(200);
  const suspended=await request.post(TENANT_PATH+"/status",{headers,data:{expected_revision:9,status:"disabled",
    confirmation:TENANT,reason:"Preserve suspended access during restoration",idempotency_key:"browser-restore-suspend-123456789"}});
  expect(suspended.status()).toBe(200);
  const initial=persisted(),before=initial.tenants;
  expect(initial.source_restore_writer_enabled).toBe(true);
  expect(before[TENANT].revision.N).toBe("10");
  const refreshed=page.waitForResponse(r=>r.url().endsWith("/v1/admin/features"));
  await page.locator("#refresh").click();await refreshed;
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Review cap restoration",exact:true}).click();
  await expect(page.locator("#cap-restore-review")).toContainText("Saved local request cap: 4,000 requests/month");
  await expect(page.locator("#cap-restore-review")).toContainText("Verified configured plan: 10,000 requests/month");
  await expect(page.locator("#cap-restore-review")).toContainText("After restoration: 10,000 requests/month");
  await expect(page.locator("#cap-restore-review")).toContainText("Tenant remains suspended");
  await expect(page.locator("#cap-restore-review")).toContainText("Test mode");
  await expect(page.locator("#cap-restore-review")).not.toContainText("cus_alpha");
  expect(providerReads()).toHaveLength(1);
  expect(persisted().tenants).toEqual(before);
  await page.locator("#cap-restore-reason").fill("Restore the reviewed saved local request cap");
  await page.locator("#cap-restore-confirm").fill("RESTORE "+TENANT);
  await expect(page.locator("#cap-restore-submit")).toBeDisabled();
  await page.locator("#cap-restore-confirm").fill("RESTORE "+TENANT+" TO 10000 REQUESTS PER MONTH");
  const writes=[];
  page.on("request",r=>{if(r.url().endsWith(PREFIX) && r.method()==="POST") writes.push(r.postDataJSON());});
  const submitted=page.waitForResponse(r=>r.url().endsWith(PREFIX) && r.request().method()==="POST");
  await page.locator("#cap-restore-submit").click();
  const response=await submitted;
  expect(response.status()).toBe(200);
  const body=response.request().postDataJSON(),applied=(await response.json()).restoration;
  expect(Object.keys(body).sort()).toEqual(["confirmation","expected_revision","idempotency_key","preview_reference","reason"]);
  expect(body.expected_revision).toBe(10);expect(body.preview_reference).toMatch(/^cr1\./);
  expect(applied).toMatchObject({status:"applied",integrity_status:"signed_verified",prior_revision:10,revision:11,
    allowance_before:4000,operator_cap_before:4000,plan_allowance:10000,allowance_after:10000,
    operator_cap_after:null,tenant_status:"disabled",subscription_status:"active",overage:"block",historical:true});
  await expect(page.locator("#cap-restore-result")).toContainText("Plan allowance restored");
  await expect(page.locator("#cap-restore-result")).toContainText("Suspension was preserved");
  const reference=await page.locator("#cap-restore-reference").inputValue();
  expect(reference).toBe(body.idempotency_key);expect(writes).toHaveLength(1);
  await expect(page.locator("#cap-restore-submit")).toBeDisabled();
  const restored=persisted(),record=JSON.parse(restored.tenants[TENANT].record.S),original=JSON.parse(before[TENANT].record.S);
  expect(record.monthly_allowance).toBe(10000);expect(record).not.toHaveProperty("admin_monthly_allowance_cap");
  const stable=value=>Object.fromEntries(Object.entries(value).filter(([key])=>
    !["monthly_allowance","admin_monthly_allowance_cap","updated_at"].includes(key)));
  expect(stable(record)).toEqual(stable(original));
  expect(restored.tenants[TENANT].usage).toEqual(before[TENANT].usage);
  expect(restored.tenants["usr-admin-beta"]).toEqual(before["usr-admin-beta"]);
  expect(providerReads()).toHaveLength(2);
  const events=restored.receipts.filter(r=>r.operation==="tenant.allowance_restore");
  expect(events).toHaveLength(1);
  expect(events[0]).toMatchObject({event_id:applied.audit_event_id,integrity_status:"signed_verified",
    prior_revision:10,new_revision:11,change:{before:4000,after:10000,plan_allowance:10000,
      operator_cap_before:4000,operator_cap_after:null,cap_removed:true,tenant_status:"disabled",subscription_status:"active"}});
  expect(restored.receipts.every(r=>r.integrity_status==="signed_verified")).toBe(true);
  const later=await request.post(TENANT_PATH+"/allowance-cap",{headers,data:{expected_revision:11,new_limit:2000,
    confirmation:TENANT,reason:"Retain a later stricter local request cap",idempotency_key:"browser-restore-later-cap-123456789"}});
  expect(later.status()).toBe(200);
  const strict=persisted().tenants;
  await page.locator("#cap-restore-receipt").click();
  await expect(page.locator("#cap-restore-receipt-result")).toContainText("Recovered signed history");
  await expect(page.locator("#cap-restore-receipt-result")).toContainText("later caps and tenant changes remain authoritative");
  const replay=await request.post(PREFIX,{headers,data:body});
  expect(replay.status()).toBe(200);expect((await replay.json()).restoration).toEqual({...applied,replayed:true});
  expect(persisted().tenants).toEqual(strict);expect(providerReads()).toHaveLength(2);
  await page.locator("#logout").click();await login(page);
  await page.locator("#nav-billing").click();
  await page.locator("#cap-restore-receipt-tenant").fill(TENANT);
  await page.locator("#cap-restore-receipt-reference").fill(reference);
  await page.locator("#cap-restore-receipt").click();
  await expect(page.locator("#cap-restore-receipt-result")).toContainText("Recovered signed history");
  await expect(page.locator("#cap-restore-receipt-result")).toContainText("Suspension was preserved");
  expect(providerReads()).toHaveLength(2);expect(persisted().tenants).toEqual(strict);
  expect(writes).toHaveLength(1);
  expect(entries(".cancellation-requests.jsonl").filter(r=>r.method==="POST")).toHaveLength(0);
  expect(entries(".refund-requests.jsonl").filter(r=>r.method==="POST")).toHaveLength(0);
  await page.locator("#logout").click();
  await expect(page.locator("#cap-restore-reference")).toHaveValue("");
  await expect(page.locator("#cap-restore-receipt-reference")).toHaveValue("");
});
