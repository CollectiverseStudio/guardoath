"use strict";

const {test,expect}=require("@playwright/test");
const {createAdminFixture,openFixtureSession}=require("./admin_fixture");
const CONTRACT="subscription-cap-restoration-v1",TENANT="sample-studio";
const REASON="Restore the reviewed saved local request cap";
const iso=ms=>new Date(ms).toISOString().replace(".000Z","Z");
const posts=fixture=>fixture.calls.filter(r=>r.method==="POST" && r.path.endsWith("/allowance-cap/restore"));

function createFixture(options={}) {
  const history=new Map();
  const state={holds:[],previewChange:null,receiptChange:null,loseReply:false,noReceipt:false,applyStatus:200};
  const fixture=createAdminFixture({nativeMfa:true,features:{billing_cap_restore_preview:true,
    billing_cap_restore_submit:true,billing_cap_restore_receipts:true,billing_configuration:{
      signed_audit:true,stripe_read:true,plan_allowance:true,cancellation_provider:true,refund_provider:true,
      subscription_writer_enabled:true,cancellation_writer_enabled:true,refund_writer_enabled:true,
      cap_restore_writer_enabled:true,...options.configuration},...options.features},override(request) {
      const tenantId=request.path.split("/")[4],tenant=fixture.tenants.find(t=>t.tenant_id===tenantId);
      const reply=(value,status=200)=>({status,contentType:"application/json",body:JSON.stringify(status===200 ?
        {restoration:value,read_audit_event_id:"a".repeat(64)}:{detail:"rk_live_private cus_private provider body"})});
      const now=Math.floor(Date.now()/1000)*1000;
      if(request.path.endsWith("/allowance-cap/restore/preview")) {
        const value={status:"reviewed",operation:CONTRACT,tenant_id:tenantId,expected_revision:tenant.revision,
          allowance_before:4000,operator_cap_before:4000,plan_allowance:10000,allowance_after:10000,
          operator_cap_after:null,tenant_status:tenant.status,subscription_status:"active",provider_status:"active",
          overage:"block",livemode:false,observed_at_utc:iso(now),expires_at_utc:iso(now+180000),
          current_period_start_utc:iso(now-3600000),current_period_end_utc:iso(now+86400000),
          cancellation:{cancel_at_period_end:false,cancel_at_utc:null},target_fingerprint:"b".repeat(64),
          preview_reference:"cr1."+"R".repeat(180),tenant_mutated:false,
          confirmation:"RESTORE "+tenantId+" TO 10000 REQUESTS PER MONTH",
          restore_available:fixture.features.billing_cap_restore_submit===true};
        if(state.previewChange) state.previewChange(value);
        state.preview=structuredClone(value);
        return reply(value);
      }
      if(request.path.endsWith("/allowance-cap/restore")) {
        if(state.applyStatus!==200) return reply(null,state.applyStatus);
        const body=request.body;
        const value={status:"applied",operation:CONTRACT,tenant_id:tenantId,action_id:"c".repeat(64),
          audit_event_id:"c".repeat(64),prior_revision:body.expected_revision,revision:body.expected_revision+1,
          allowance_before:4000,operator_cap_before:4000,plan_allowance:10000,allowance_after:10000,
          operator_cap_after:null,tenant_status:tenant.status,subscription_status:"active",overage:"block",
          reason:body.reason,recorded_at_utc:iso(now),target_fingerprint:"b".repeat(64),
          integrity_status:"signed_verified",replayed:false,historical:true};
        if(!state.noReceipt) {
          history.set(tenantId+"/"+body.idempotency_key,{...structuredClone(value),replayed:true});
          tenant.monthly_allowance=10000;tenant.revision=value.revision;
        }
        return state.loseReply ? reply(null,503):reply(value);
      }
      if(request.path.endsWith("/allowance-cap/restore/receipt")) {
        const value=structuredClone(history.get(tenantId+"/"+request.body.idempotency_key) || {
          status:"not_found",operation:CONTRACT,tenant_id:tenantId,action_id:"c".repeat(64),tenant_mutated:false});
        if(state.receiptChange) state.receiptChange(value);
        return reply(value);
      }
    }});
  fixture.tenants[0].monthly_allowance=4000;
  if(options.status) fixture.tenants[0].status=options.status;
  fixture.state=state;fixture.history=history;
  return fixture;
}

async function setup(page,options={}) {
  const fixture=createFixture(options);
  await page.route("**/*",async route=>{
    const request=route.request(),reply=fixture.resolve(request.url(),request.method(),request.headers(),request.postData());
    const held=fixture.state.holds.find(h=>!h.used && request.url().endsWith(h.suffix));
    if(held) {held.used=true;held.url=request.url();held.enter();await held.wait;}
    await route.fulfill(reply);
  });
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  return fixture;
}
async function review(page) {
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Review cap restoration",exact:true}).click();
  await expect(page.locator("#cap-restore-review")).toContainText("After restoration: 10,000 requests/month");
}
async function confirm(page,fixture) {
  await page.locator("#cap-restore-confirm").fill(fixture.state.preview.confirmation);
  await page.locator("#cap-restore-reason").fill(REASON);
  await expect(page.locator("#cap-restore-submit")).toBeEnabled();
}
function holdNext(fixture,suffix) {
  let enter,release;
  const held={suffix,used:false,entered:new Promise(resolve=>{enter=resolve;}),wait:new Promise(resolve=>{release=resolve;})};
  held.enter=enter;held.release=release;fixture.state.holds.push(held);return held;
}
async function release(page,held) {
  const returned=page.waitForResponse(r=>r.url()===held.url);
  held.release();expect(await (await returned).finished()).toBeNull();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}

test("exact reviewed restoration shows saved cap, plan and preserved suspension before one submission",async({page})=>{
  const fixture=await setup(page,{status:"disabled"});await review(page);
  await expect(page.locator("#cap-restore-review")).toContainText("Saved local request cap: 4,000 requests/month");
  await expect(page.locator("#cap-restore-review")).toContainText("Verified configured plan: 10,000 requests/month");
  await expect(page.locator("#cap-restore-review")).toContainText("Tenant remains suspended");
  await expect(page.locator("#cap-restore-review")).toContainText("Test mode");
  await page.locator("#cap-restore-reason").fill(REASON);
  await page.locator("#cap-restore-confirm").fill("RESTORE "+TENANT);
  await expect(page.locator("#cap-restore-submit")).toBeDisabled();
  expect(posts(fixture)).toHaveLength(0);
  await confirm(page,fixture);await page.locator("#cap-restore-submit").click();
  await expect(page.locator("#cap-restore-result")).toContainText("Plan allowance restored");
  await expect(page.locator("#cap-restore-result")).toContainText("Suspension was preserved");
  expect(posts(fixture)).toHaveLength(1);
  expect(Object.keys(posts(fixture)[0].body).sort()).toEqual([
    "confirmation","expected_revision","idempotency_key","preview_reference","reason"]);
  await page.locator("#cap-restore-receipt").click();
  await expect(page.locator("#cap-restore-receipt-result")).toContainText("Recovered signed history");
  expect(posts(fixture)).toHaveLength(1);
});

test("lost response retains its reference and recovers signed history without another submission",async({page})=>{
  const fixture=await setup(page);fixture.state.loseReply=true;
  await review(page);await confirm(page,fixture);await page.locator("#cap-restore-submit").click();
  await expect(page.locator("#cap-restore-result")).toContainText("Restoration outcome unverified");
  await expect(page.locator("#cap-restore-result")).not.toContainText("private");
  await expect(page.locator("#cap-restore-reference")).not.toHaveValue("");
  await page.locator("#cap-restore-receipt").click();
  await expect(page.locator("#cap-restore-receipt-result")).toContainText("Recovered signed history");
  expect(posts(fixture)).toHaveLength(1);
});

test("missing receipt after timeout does not authorize a new restoration",async({page})=>{
  const fixture=await setup(page);fixture.state.loseReply=true;fixture.state.noReceipt=true;
  await review(page);await confirm(page,fixture);await page.locator("#cap-restore-submit").click();
  await expect(page.locator("#cap-restore-result")).toContainText("Restoration outcome unverified");
  const reference=await page.locator("#cap-restore-reference").inputValue();
  await page.locator("#cap-restore-receipt").click();
  await expect(page.locator("#cap-restore-receipt-result")).toContainText("A timed-out request may still complete");
  await expect(page.locator("#cap-restore-reference")).toHaveValue(reference);
  await page.locator("#nav-tenants").click();
  for(const button of await page.locator("[data-cap-restore]").all()) await expect(button).toBeDisabled();
  expect(posts(fixture)).toHaveLength(1);
});

test("definitive rejection consumes the review and permits an explicit fresh review",async({page})=>{
  const fixture=await setup(page);fixture.state.applyStatus=409;
  await review(page);await confirm(page,fixture);await page.locator("#cap-restore-submit").click();
  await expect(page.locator("#cap-restore-result")).toContainText("Restoration rejected");
  const reference=await page.locator("#cap-restore-reference").inputValue();
  await expect(page.locator("#cap-restore-submit")).toBeDisabled();
  fixture.state.applyStatus=200;
  await review(page);await confirm(page,fixture);await page.locator("#cap-restore-submit").click();
  await expect(page.locator("#cap-restore-result")).toContainText("Plan allowance restored");
  expect(posts(fixture)).toHaveLength(2);
  expect(posts(fixture)[1].body.idempotency_key).not.toBe(reference);
});

test("explicit disabled source retains review and signed history with no restore control",async({page})=>{
  const fixture=await setup(page,{features:{billing_cap_restore_submit:false},configuration:{cap_restore_writer_enabled:false}});
  await review(page);
  await expect(page.locator("#cap-restore-review")).toContainText("Restoration is unavailable in this release");
  await expect(page.locator("#cap-restore-confirm")).toBeDisabled();
  await expect(page.locator("#cap-restore-submit")).toBeDisabled();
  await expect(page.locator("#cap-restore-receipt")).toBeEnabled();
  expect(posts(fixture)).toHaveLength(0);
});

for(const [name,change] of [
  ["tenant",v=>v.tenant_id="other-tenant"],["revision",v=>v.expected_revision++],
  ["saved cap",v=>v.operator_cap_before=3000],["plan",v=>v.plan_allowance=20000],
  ["cap removal",v=>v.operator_cap_after=4000],["provider status",v=>v.provider_status="past_due"],
  ["mode",v=>v.livemode="false"],["period",v=>v.current_period_end_utc=v.observed_at_utc],
  ["expiry after period",v=>v.current_period_end_utc=iso(Date.parse(v.expires_at_utc)-1000)],
  ["cancellation",v=>v.cancellation.cancel_at_utc=v.observed_at_utc],
  ["target",v=>v.target_fingerprint="unknown"],["token",v=>v.preview_reference="untrusted"],
  ["confirmation",v=>v.confirmation="RESTORE ALL"],["expired",v=>v.expires_at_utc=iso(Date.now()-60000)]]) {
  test("unverified restoration review never enables submission: "+name,async({page})=>{
    const fixture=await setup(page);fixture.state.previewChange=change;
    await page.locator("#nav-tenants").click();
    await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Review cap restoration",exact:true}).click();
    await expect(page.locator("#cap-restore-review")).toContainText("No restoration was submitted");
    await expect(page.locator("#cap-restore-submit")).toBeDisabled();
    expect(posts(fixture)).toHaveLength(0);
  });
}

for(const [name,change] of [
  ["allowance",v=>v.allowance_after=20000],["prior cap",v=>v.operator_cap_before=3000],
  ["revision",v=>v.prior_revision++],["target",v=>v.target_fingerprint="f".repeat(64)],
  ["audit link",v=>v.audit_event_id="f".repeat(64)],["signature",v=>v.integrity_status="unverified"],
  ["status",v=>v.tenant_status="disabled"],["historical",v=>v.historical=false]]) {
  test("unverified restoration history preserves the retained action: "+name,async({page})=>{
    const fixture=await setup(page);fixture.state.receiptChange=change;
    await review(page);await confirm(page,fixture);await page.locator("#cap-restore-submit").click();
    await expect(page.locator("#cap-restore-result")).toContainText("Restoration outcome unverified");
    await expect(page.locator("#cap-restore-reference")).not.toHaveValue("");
    expect(posts(fixture)).toHaveLength(1);
  });
}

test("in-flight restoration blocks duplicate submission and new reviews",async({page})=>{
  const fixture=await setup(page);await review(page);await confirm(page,fixture);
  const held=holdNext(fixture,"/allowance-cap/restore");
  await page.locator("#cap-restore-submit").click();await held.entered;
  await expect(page.locator("#cap-restore-submit")).toBeDisabled();
  await expect(page.locator("#cap-restore-receipt")).toBeDisabled();
  expect(posts(fixture)).toHaveLength(1);
  await release(page,held);await expect(page.locator("#cap-restore-result")).toContainText("Plan allowance restored");
});

test("sign-out withdraws an in-flight restoration review",async({page})=>{
  const fixture=await setup(page),held=holdNext(fixture,"/allowance-cap/restore/preview");
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Review cap restoration",exact:true}).click();
  await held.entered;await page.locator("#logout").click();await release(page,held);
  await expect(page.locator("#app")).toBeHidden();
  await expect(page.locator("#cap-restore-confirm")).toHaveValue("");
  await expect(page.locator("#cap-restore-reference")).toHaveValue("");
  await expect(page.locator("#cap-restore-submit")).toBeDisabled();
  expect(posts(fixture)).toHaveLength(0);
});
