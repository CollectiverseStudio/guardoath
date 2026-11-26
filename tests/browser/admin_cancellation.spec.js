"use strict";

const {test,expect} = require("@playwright/test");
const {createAdminFixture,openFixtureSession} = require("./admin_fixture");

const CONTRACT = "stripe-period-end-cancellation-v1";
const TENANT = "sample-studio";
const REASON = "Owner requested synthetic period end cancellation";
const iso = ms => new Date(ms).toISOString().replace(".000Z","Z");
const posts = fixture => fixture.calls.filter(r=>r.path.endsWith("/cancel-at-period-end") && r.method==="POST");

function setupFixture(options={}) {
  const history = new Map();
  const state = {holds:[],previewSeconds:180,previewChange:null,outcomeChange:null,receiptChange:null,status:200};
  const fixture = createAdminFixture({nativeMfa:true,features:{
    billing_cancellation_preview:true,billing_cancellation_schedule:true,billing_cancellation_receipts:true,
    ...options.features},override(request) {
      const tenantId = request.path.split("/")[4];
      const tenant = fixture.tenants.find(value=>value.tenant_id===tenantId);
      const json = cancellation => ({status:state.status,contentType:"application/json",
        body:JSON.stringify(state.status === 200 ? {cancellation,read_audit_event_id:"a".repeat(64)} :
          {detail:"rk_live_private_provider_body cus_private_customer"})});
      if (request.path.endsWith("/cancellation/preview")) {
        const now = Math.floor(Date.now()/1000)*1000;
        const value = {status:"reviewed",operation:CONTRACT,tenant_id:tenantId,expected_revision:tenant.revision,
          observed_at_utc:iso(now),effective_at_utc:iso(now+86400000),expires_at_utc:iso(now+state.previewSeconds*1000),
          target_fingerprint:(tenantId===TENANT?"b":"c").repeat(64),preview_reference:"sc1."+"A".repeat(200),
          confirmation:"SCHEDULE CANCELLATION "+tenantId,scheduling_available:options.features?.billing_cancellation_schedule!==false,
          tenant_mutated:false};
        state.lastPreview = structuredClone(value);
        if (state.previewChange) state.previewChange(value);
        return json(value);
      }
      if (request.path.endsWith("/cancel-at-period-end")) {
        const body = request.body, now = Math.floor(Date.now()/1000)*1000;
        const intent = {action_id:"d".repeat(64),intent_hash:"e".repeat(64),
          actor_sub:"12345678-1234-1234-1234-123456789012",tenant_id:tenantId,expected_revision:body.expected_revision,
          reason:body.reason,generation:1,target_fingerprint:state.lastPreview.target_fingerprint,
          effective_at_utc:state.lastPreview.effective_at_utc,created_at_utc:iso(now),before:false,after:true};
        const value = {operation:CONTRACT,tenant_id:tenantId,action_reference:body.idempotency_key,
          status:"verified",integrity_status:"signed_verified",tenant_mutated:false,provider_state_refreshed:false,intent,
          outcome:{action_id:intent.action_id,intent_hash:intent.intent_hash,status:"verified",recorded_at_utc:iso(now)}};
        if (state.outcomeChange) state.outcomeChange(value);
        history.set(tenantId+"/"+body.idempotency_key,structuredClone(value));
        return json(value);
      }
      if (request.path.endsWith("/cancellation/receipt")) {
        const value = structuredClone(history.get(tenantId+"/"+request.body.idempotency_key) || {
          operation:CONTRACT,tenant_id:tenantId,action_reference:request.body.idempotency_key,status:"not_found",
          integrity_status:"not_found",tenant_mutated:false,provider_state_refreshed:false,intent:null,outcome:null});
        if (state.receiptChange) state.receiptChange(value);
        return json(value);
      }
    }});
  fixture.state = state;
  fixture.history = history;
  return fixture;
}

async function setup(page,options={}) {
  const fixture = setupFixture(options);
  await page.route("**/*",async route=>{
    const req = route.request(), response = fixture.resolve(req.url(),req.method(),req.headers(),req.postData());
    const hold = fixture.state.holds.find(value=>!value.used && value.match(req.url()));
    if (hold) {hold.used=true;hold.url=req.url();hold.enter();await hold.wait;}
    await route.fulfill(response);
  });
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-tenants").click();
  return fixture;
}

function holdNext(fixture,suffix) {
  let enter,release;
  const value = {used:false,match:url=>url.endsWith(suffix),
    entered:new Promise(resolve=>{enter=resolve;}),wait:new Promise(resolve=>{release=resolve;})};
  value.enter=enter;value.release=release;fixture.state.holds.push(value);return value;
}

async function finishHeld(page,held) {
  const returned = page.waitForResponse(response=>response.url()===held.url);
  held.release();
  const response = await returned;
  expect(await response.finished()).toBeNull();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}

async function review(page,tenant=TENANT) {
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:tenant}).getByRole("button",{name:"Review cancellation",exact:true}).click();
  await expect(page.locator("#cancellation-review")).toContainText("Scheduled end UTC");
}

async function fill(page,tenant=TENANT) {
  await page.locator("#cancellation-confirm").fill("SCHEDULE CANCELLATION "+tenant);
  await page.locator("#cancellation-reason").fill(REASON);
  await expect(page.locator("#cancellation-schedule")).toBeEnabled();
}

test("confirmed schedule shows linked historical outcome and recovery never resends",async ({page})=>{
  const fixture = await setup(page);
  await review(page);
  await page.locator("#cancellation-reason").fill(REASON);
  await page.locator("#cancellation-confirm").fill("wrong");
  await expect(page.locator("#cancellation-schedule")).toBeDisabled();
  await fill(page);
  await page.locator("#cancellation-schedule").click();
  await expect(page.locator("#cancellation-result")).toContainText("Verified historical schedule");
  await expect(page.locator("#cancellation-result")).toContainText("Current provider state was not refreshed");
  expect(posts(fixture)).toHaveLength(1);
  const sent = posts(fixture)[0].body;
  expect(Object.keys(sent).sort()).toEqual(["confirmation","expected_revision","idempotency_key","preview_reference","reason"]);
  expect(await page.locator("#cancellation-reference").inputValue()).toBe(sent.idempotency_key);
  await page.locator("#cancellation-receipt").click();
  await expect(page.locator("#cancellation-receipt-result")).toContainText("Verified historical schedule");
  await expect(page.locator("#cancellation-receipt-result")).toContainText("Recovered signed history");
  expect(posts(fixture)).toHaveLength(1);
  expect(await page.evaluate(()=>JSON.stringify([localStorage,sessionStorage]))).not.toContain(sent.preview_reference);
});

const previewFaults = {
  tenant:v=>{v.tenant_id="sample-labs";},revision:v=>{v.expected_revision++;},contract:v=>{v.operation="refund";},
  expired:v=>{v.expires_at_utc="2020-01-01T00:00:00Z";},invalidDate:v=>{v.effective_at_utc="2026-02-31T00:00:00Z";},
  longWindow:v=>{v.expires_at_utc=iso(Date.parse(v.observed_at_utc)+181000);},
  token:v=>{v.preview_reference="https://private.invalid";},fingerprint:v=>{v.target_fingerprint="sub_private";},
  confirmation:v=>{v.confirmation="SCHEDULE CANCELLATION sample-labs";},mutation:v=>{v.tenant_mutated=true;},
  missingAvailability:v=>{delete v.scheduling_available;},wrongType:v=>{v.expected_revision="4";}
};
for (const [name,change] of Object.entries(previewFaults)) {
  test("unverified cancellation preview refuses "+name,async ({page})=>{
    const fixture = await setup(page);fixture.state.previewChange=change;
    await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Review cancellation",exact:true}).click();
    await expect(page.locator("#cancellation-review")).toContainText("Cancellation review unavailable");
    await expect(page.locator("#cancellation-schedule")).toBeDisabled();
    expect(posts(fixture)).toHaveLength(0);
  });
}

const outcomeFaults = {
  tenant:v=>{v.tenant_id="sample-labs";},reference:v=>{v.action_reference="foreign-action-123456789";},
  missingOutcome:v=>{v.outcome=null;},link:v=>{v.outcome.intent_hash="f".repeat(64);},
  revision:v=>{v.intent.expected_revision++;},reason:v=>{v.intent.reason="Foreign confirmed cancellation reason";},
  effective:v=>{v.intent.effective_at_utc="2020-01-01T00:00:00Z";},
  mutation:v=>{v.tenant_mutated=true;},current:v=>{v.provider_state_refreshed=true;},
  integrity:v=>{v.integrity_status="legacy_unverified";}
};
for (const [name,change] of Object.entries(outcomeFaults)) {
  test("unverified schedule outcome retains reference for "+name,async ({page})=>{
    const fixture=await setup(page);fixture.state.outcomeChange=change;
    await review(page);await fill(page);await page.locator("#cancellation-schedule").click();
    await expect(page.locator("#cancellation-result")).toContainText("Cancellation outcome unverified");
    await expect(page.locator("#cancellation-reference")).not.toHaveValue("");
    await page.locator("#nav-tenants").click();
    await page.locator("#tenant-rows tr").filter({hasText:"sample-labs"}).getByRole("button",{name:"Review cancellation",exact:true}).click();
    await expect(page.locator("#cancellation-result")).toContainText("inspect its signed history");
    expect(posts(fixture)).toHaveLength(1);
  });
}

for (const code of [409,429,503]) {
  test("schedule "+code+" does not retry or expose private provider errors",async ({page})=>{
    const fixture=await setup(page);await review(page);await fill(page);fixture.state.status=code;
    await page.locator("#cancellation-schedule").click();
    await expect(page.locator("#cancellation-result")).toContainText("Cancellation outcome unverified");
    expect(posts(fixture)).toHaveLength(1);
    await expect(page.locator("#cancellation-schedule")).toBeDisabled();
    await expect(page.locator("body")).not.toContainText("private_provider_body");
    await expect(page.locator("body")).not.toContainText("cus_private_customer");
  });
}

test("refresh and repeated clicks cannot dispatch a second in-flight operation",async ({page})=>{
  const fixture=await setup(page);await review(page);await fill(page);
  const held=holdNext(fixture,"/cancel-at-period-end");
  await page.evaluate(()=>{document.querySelector("#cancellation-schedule").click();document.querySelector("#cancellation-schedule").click();});
  await held.entered;
  await expect(page.locator("#cancellation-result")).toContainText("Pending provider confirmation");
  await page.locator("#refresh").click();
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-labs"}).getByRole("button",{name:"Review cancellation",exact:true}).click();
  expect(posts(fixture)).toHaveLength(1);
  await finishHeld(page,held);
  await expect(page.locator("#cancellation-result")).not.toContainText("Verified historical schedule");
  await page.locator("#cancellation-receipt").click();
  await expect(page.locator("#cancellation-receipt-result")).toContainText("Verified historical schedule");
  expect(posts(fixture)).toHaveLength(1);
});

test("accepted dispatch completes across review expiry without losing its receipt check",async ({page})=>{
  await page.clock.install();
  const fixture=await setup(page);fixture.state.previewSeconds=60;
  await review(page);await fill(page);
  const held=holdNext(fixture,"/cancel-at-period-end");
  await page.locator("#cancellation-schedule").click();await held.entered;
  await page.clock.fastForward(65000);
  await finishHeld(page,held);
  await expect(page.locator("#cancellation-result")).toContainText("Verified historical schedule");
  expect(fixture.calls.filter(r=>r.path.endsWith("/cancellation/receipt"))).toHaveLength(1);
  expect(posts(fixture)).toHaveLength(1);
});

test("an older held receipt cannot appear beneath a newly submitted reference",async ({page})=>{
  const fixture=await setup(page);
  await page.locator("#nav-billing").click();
  await page.locator("#cancellation-receipt-tenant").fill(TENANT);
  await page.locator("#cancellation-receipt-reference").fill("old-reference-123456789");
  const held=holdNext(fixture,"/cancellation/receipt");
  await page.locator("#cancellation-receipt").click();await held.entered;
  await review(page);await fill(page);await page.locator("#cancellation-schedule").click();
  await expect(page.locator("#cancellation-result")).toContainText("Verified historical schedule");
  await finishHeld(page,held);
  await expect(page.locator("#cancellation-receipt-result")).toHaveText("");
  await expect(page.locator("#cancellation-receipt-reference")).not.toHaveValue("old-reference-123456789");
});

for (const transition of ["other tenant","refresh","logout","verify","invoices"]) {
  test("late cancellation preview cannot replace "+transition,async ({page})=>{
    const fixture=await setup(page);
    const held=holdNext(fixture,"/cancellation/preview");
    await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Review cancellation",exact:true}).click();
    await held.entered;
    if (transition==="refresh") {await page.locator("#refresh").click();await expect(page.locator("#status")).toContainText("Administration loaded");}
    else if (transition==="logout") await page.locator("#logout").click();
    else {
      await page.locator("#nav-tenants").click();
      const target=page.locator("#tenant-rows tr").filter({hasText:"sample-labs"});
      await target.getByRole("button",{name:transition==="other tenant"?"Review cancellation":transition==="verify"?"Verify Stripe":"Invoices",exact:true}).click();
      if(transition==="other tenant") await expect(page.locator("#cancellation-review")).toContainText("sample-labs");
    }
    await finishHeld(page,held);
    await expect(page.locator("#cancellation-review")).not.toContainText("SCHEDULE CANCELLATION "+TENANT);
    expect(posts(fixture)).toHaveLength(0);
  });
}

test("indeterminate and absent history do not imply current provider success",async ({page})=>{
  const fixture=await setup(page);
  fixture.state.outcomeChange=value=>{value.status="indeterminate";value.outcome=null;};
  await review(page);await fill(page);await page.locator("#cancellation-schedule").click();
  await expect(page.locator("#cancellation-result")).toContainText("Indeterminate cancellation");
  await expect(page.locator("#cancellation-result")).toContainText("Provider outcome ambiguous");
  await page.locator("#cancellation-receipt").click();
  await expect(page.locator("#cancellation-receipt-result")).toContainText("Indeterminate cancellation");
  await page.locator("#cancellation-receipt-reference").fill("different-unknown-reference");
  await page.locator("#cancellation-receipt").click();
  await expect(page.locator("#cancellation-receipt-result")).toContainText("No signed cancellation intent found");
  await expect(page.locator("#cancellation-result")).not.toContainText("Verified historical schedule");
  expect(posts(fixture)).toHaveLength(1);
});

test("support-only capability stays visible and read-only at mobile width",async ({page})=>{
  await page.setViewportSize({width:390,height:844});
  const fixture=await setup(page,{features:{billing_cancellation_schedule:false}});
  await review(page);
  await expect(page.locator("#cancellation-review")).toContainText("Scheduling is unavailable in this release");
  await expect(page.locator("#cancellation-schedule")).toBeDisabled();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  expect(posts(fixture)).toHaveLength(0);
});

for (const scenario of [
  {name:"disabled source",configuration:{subscription_writer_enabled:false,cancellation_writer_enabled:false},
    features:{billing_reconcile:false,billing_cancellation_schedule:false},
    target:"#billing-cancellation-capability",message:"Unavailable in this release",review:true},
  {name:"missing cancellation provider",configuration:{cancellation_provider:false},
    features:{billing_reconcile:true,billing_cancellation_preview:false,billing_cancellation_schedule:false},
    target:"#billing-cancellation-capability",message:"Requires the account-bound cancellation provider",review:false},
  {name:"missing request plan",configuration:{plan_allowance:false},
    features:{billing_reconcile:false},target:"#billing-reconcile-capability",
    message:"Requires a configured request allowance",review:true},
  {name:"configured prerequisites without services",configuration:{},
    features:{billing_reconcile:false,billing_cancellation_preview:false,billing_cancellation_schedule:false},
    target:"#billing-cancellation-capability",message:"Required owner services are not mounted",review:false},
]) {
  test("financial controls explain "+scenario.name+" without claiming provider verification",async ({page})=>{
    const fixture=await setup(page,{features:{...scenario.features,billing_configuration:{
      signed_audit:true,stripe_read:true,plan_allowance:true,cancellation_provider:true,
      subscription_writer_enabled:true,cancellation_writer_enabled:true,...scenario.configuration}}});
    await page.locator("#nav-billing").click();
    await expect(page.locator("#billing-configuration-status")).toContainText("Review a tenant to verify its provider state");
    await expect(page.locator(scenario.target)).toContainText(scenario.message);
    await expect(page.locator("#billing-history-capability")).toContainText("recovery never resends");
    await page.locator("#billing-choose-tenant").click();
    await expect(page.locator("#section-tenants")).toBeVisible();
    await expect(page.getByRole("button",{name:"Review cancellation",exact:true})).toHaveCount(scenario.review?2:0);
    expect(fixture.calls.filter(r=>r.path.includes("/v1/admin/subscriptions/") && r.method==="POST")).toHaveLength(0);
  });
}

test("capability details are withdrawn after a failed authenticated refresh",async ({page})=>{
  const fixture=await setup(page,{features:{billing_configuration:{signed_audit:true,stripe_read:true,
    plan_allowance:true,cancellation_provider:true,subscription_writer_enabled:true,cancellation_writer_enabled:true}}});
  await page.locator("#nav-billing").click();
  await expect(page.locator("#billing-cancellation-capability")).toContainText("Available");
  const resolve=fixture.override;
  fixture.override=request=>request.path==="/v1/admin/features" ?
    {status:503,contentType:"application/json",body:JSON.stringify({detail:"Source unavailable"})}:resolve(request);
  await page.locator("#refresh").click();
  await expect(page.locator("#billing-configuration-status")).toContainText("one or more protected owner sources failed");
  await expect(page.locator("#billing-cancellation-capability")).toBeEmpty();
  await expect(page.locator("#cancellation-schedule")).toBeDisabled();
  expect(posts(fixture)).toHaveLength(0);
});


for (const code of [401,403]) {
  test("late cancellation "+code+" cannot sign out a reauthenticated owner",async ({page})=>{
    const fixture=await setup(page);await review(page);await fill(page);
    fixture.state.status=code;
    const held=holdNext(fixture,"/cancel-at-period-end");
    await page.locator("#cancellation-schedule").click();await held.entered;
    await page.locator("#logout").click();fixture.state.status=200;
    await page.locator("#native-email").fill("owner@example.invalid");
    await page.locator("#native-password").fill("synthetic-test-password-only");
    await page.locator("#native-start").click();
    await expect(page.locator("#native-challenge")).toBeVisible();
    await page.locator("#native-code").fill("123456");
    await page.locator("#native-finish").click();
    await expect(page.locator("#status")).toContainText("Administration loaded");
    await finishHeld(page,held);
    await expect(page.locator("#app")).toBeVisible();
    await expect(page.locator("#cancellation-reference")).toHaveValue("");
    await expect(page.locator("#cancellation-result")).toHaveText("");
    expect(posts(fixture)).toHaveLength(1);
  });
}

for (const operation of ["downgrade","reconcile"]) {
  test("confirmed "+operation+" invalidates an older cancellation preview",async ({page})=>{
    const fixture=await setup(page,{features:{billing_downgrade:true,billing_reconcile:true}});
    const original=fixture.override;
    fixture.override=(request,f)=>request.path.endsWith("/"+operation) ?
      {status:503,contentType:"application/json",body:JSON.stringify({detail:"private provider body"})} : original(request,f);
    const held=holdNext(fixture,"/cancellation/preview");
    await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Review cancellation",exact:true}).click();
    await held.entered;
    const answers=["RECONCILE "+TENANT,"Verify synthetic entitlement operation"];
    page.on("dialog",async dialog=>dialog.type()==="confirm" ? dialog.accept() : dialog.accept(answers.shift()));
    await page.locator("#nav-tenants").click();
    await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{
      name:operation==="downgrade"?"Reconcile unpaid":"Reconcile subscription",exact:true}).click();
    await finishHeld(page,held);
    await expect(page.locator("#cancellation-review")).not.toContainText("Scheduled end UTC");
    await expect(page.locator("#cancellation-schedule")).toBeDisabled();
    expect(posts(fixture)).toHaveLength(0);
    expect(answers).toEqual([]);
  });
}


for (const fault of ["missing outcome","changed link"]) {
  test("independent recovery refuses "+fault+" after a valid dispatch response",async ({page})=>{
    const fixture=await setup(page);
    fixture.state.receiptChange=value=>{
      if(fault==="missing outcome") value.outcome=null;
      else value.outcome.intent_hash="f".repeat(64);
    };
    await review(page);await fill(page);await page.locator("#cancellation-schedule").click();
    await expect(page.locator("#cancellation-result")).toContainText("Cancellation outcome unverified");
    await expect(page.locator("#cancellation-reference")).not.toHaveValue("");
    expect(posts(fixture)).toHaveLength(1);
    await expect(page.locator("#cancellation-schedule")).toBeDisabled();
  });
}
