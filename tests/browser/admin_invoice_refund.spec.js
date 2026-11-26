"use strict";

const {test,expect} = require("@playwright/test");
const {createAdminFixture,openFixtureSession} = require("./admin_fixture");
const CONTRACT = "stripe-invoice-refund-v1";
const TENANT = "sample-studio";
const REASON = "Account owner requested the reviewed invoice refund";
const iso = ms => new Date(ms).toISOString().replace(".000Z","Z");
const dispatches = fixture => fixture.calls.filter(r=>r.path.endsWith("/refund") && r.method==="POST");

function setupFixture(options={}) {
  const history = new Map();
  const state = {holds:[],status:200,previewChange:null,receiptChange:null,invoiceChange:null,
    resultStatus:"verified",refreshStatus:"verified",loseReply:false,
    amount:options.amount || 2900,currency:options.currency || "usd",exponent:options.exponent ?? 2};
  const config = {signed_audit:true,stripe_read:true,plan_allowance:true,cancellation_provider:true,
    refund_provider:true,subscription_writer_enabled:true,cancellation_writer_enabled:true,refund_writer_enabled:true,
    ...options.configuration};
  const fixture = createAdminFixture({nativeMfa:true,features:{
    billing_refund_preview:true,billing_refund_submit:true,billing_refund_receipts:true,billing_refund_refresh:true,
    billing_configuration:config,...options.features},override(request) {
      const tenantId=request.path.split("/")[4], tenant=fixture.tenants.find(t=>t.tenant_id===tenantId);
      const response = (value, status=state.status) => ({status,contentType:"application/json",
        body:JSON.stringify(status===200 ? value : {detail:"rk_live_private cus_private provider body"})});
      const envelope = refund => response({refund,read_audit_event_id:"a".repeat(64)});
      if (request.path.endsWith("/invoices")) {
        const row={created_utc:"2026-11-20T12:00:00Z",status:"paid",currency:state.currency,
          amount_due_minor:state.amount,amount_paid_minor:state.amount,amount_remaining_minor:0,
          collection_method:"charge_automatically",attempted:true,attempt_count:1,next_payment_attempt_utc:null,
          invoice_reference:"ir1."+"I".repeat(160)};
        if (state.invoiceChange) state.invoiceChange(row);
        return response({tenant_id:tenantId,read_audit_event_id:"a".repeat(64),history:{
          status:"verified",verified:true,invoices:[row],count:1,has_more:false,next_cursor:null,
          page_number:1,status_filter:request.url.searchParams.get("status"),expected_revision:tenant.revision,
          read_at_utc:iso(Math.floor(Date.now()/1000)*1000)}});
      }
      if (request.path.endsWith("/refund/preview")) {
        const now=Math.floor(Date.now()/1000)*1000;
        const value={status:"reviewed",operation:CONTRACT,tenant_id:tenantId,expected_revision:tenant.revision,
          observed_at_utc:iso(now),expires_at_utc:iso(now+180000),target_fingerprint:"b".repeat(64),
          invoice_fingerprint:"c".repeat(64),preview_reference:"rf1."+"R".repeat(180),amount_minor:state.amount,
          currency:state.currency,currency_exponent:state.exponent,livemode:false,tenant_mutated:false,
          confirmation:"REFUND "+tenantId+" "+state.amount+" "+state.currency.toUpperCase()+" MINOR UNITS",
          refund_available:fixture.features.billing_refund_submit===true};
        if(state.previewChange) state.previewChange(value);
        state.preview=structuredClone(value);
        return envelope(value);
      }
      if(request.path.endsWith("/refund")) {
        const body=request.body, now=Math.floor(Date.now()/1000)*1000;
        const intent={action_id:"d".repeat(64),intent_hash:"e".repeat(64),
          actor_sub:"12345678-1234-1234-1234-123456789012",tenant_id:tenantId,
          expected_revision:body.expected_revision,reason:body.reason,generation:1,
          target_fingerprint:"b".repeat(64),invoice_fingerprint:"c".repeat(64),amount_minor:state.amount,
          currency:state.currency,currency_exponent:state.exponent,livemode:false,
          created_at_utc:iso(now),before:0,after:state.amount};
        const provider={verified:"succeeded",pending:"pending",failed:"failed",indeterminate:null};
        const value={operation:CONTRACT,tenant_id:tenantId,action_reference:body.idempotency_key,
          status:state.resultStatus,integrity_status:"signed_verified",tenant_mutated:false,
          provider_state_refreshed:false,intent,outcome:{action_id:intent.action_id,intent_hash:intent.intent_hash,
            status:state.resultStatus,provider_status:provider[state.resultStatus],recorded_at_utc:iso(now)}};
        history.set(tenantId+"/"+body.idempotency_key,structuredClone(value));
        return state.loseReply ? response(null,503) : envelope(value);
      }
      if(request.path.endsWith("/refund/receipt") || request.path.endsWith("/refund/refresh")) {
        const key=tenantId+"/"+request.body.idempotency_key;
        const value=structuredClone(history.get(key) || {operation:CONTRACT,tenant_id:tenantId,
          action_reference:request.body.idempotency_key,status:"not_found",integrity_status:"not_found",
          tenant_mutated:false,provider_state_refreshed:false,intent:null,outcome:null});
        if(request.path.endsWith("/refund/refresh") && value.intent) {
          value.status=state.refreshStatus;
          value.provider_state_refreshed=true;
          value.outcome.status=state.refreshStatus;
          value.outcome.provider_status={verified:"succeeded",pending:"pending",failed:"failed",indeterminate:null}[state.refreshStatus];
          value.outcome.recorded_at_utc=iso(Math.floor(Date.now()/1000)*1000);
          history.set(key,{...structuredClone(value),provider_state_refreshed:false});
        }
        if(state.receiptChange) state.receiptChange(value);
        return envelope(value);
      }
    }});
  fixture.state=state;fixture.history=history;
  return fixture;
}

async function setup(page,options={}) {
  const fixture=setupFixture(options);
  await page.route("**/*",async route=>{
    const r=route.request(),response=fixture.resolve(r.url(),r.method(),r.headers(),r.postData());
    const hold=fixture.state.holds.find(h=>!h.used && r.url().endsWith(h.suffix));
    if(hold) {hold.used=true;hold.url=r.url();hold.enter();await hold.wait;}
    await route.fulfill(response);
  });
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  return fixture;
}
async function invoices(page) {
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Invoices",exact:true}).click();
  await expect(page.locator("#invoice-details")).toContainText("Direct Stripe");
}
async function review(page) {
  await invoices(page);
  await page.locator("#invoice-refund-actions").getByRole("button").click();
  await expect(page.locator("#refund-review")).toContainText("Full refund");
}
async function fill(page,fixture) {
  await page.locator("#refund-confirm").fill(fixture.state.preview.confirmation);
  await page.locator("#refund-reason").fill(REASON);
  await expect(page.locator("#refund-submit")).toBeEnabled();
}
function holdNext(fixture,suffix) {
  let enter,release;
  const hold={suffix,used:false,entered:new Promise(resolve=>{enter=resolve;}),wait:new Promise(resolve=>{release=resolve;})};
  hold.enter=enter;hold.release=release;fixture.state.holds.push(hold);return hold;
}
async function release(page,hold) {
  const returned=page.waitForResponse(r=>r.url()===hold.url);
  hold.release();
  expect(await (await returned).finished()).toBeNull();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}

test("owner reviews exact invoice amount and mode before one confirmed refund",async({page})=>{
  const fixture=await setup(page);await review(page);
  await expect(page.locator("#refund-review")).toContainText("USD 29.00 (2900 minor units)");
  await expect(page.locator("#refund-review")).toContainText("Test mode");
  expect(dispatches(fixture)).toHaveLength(0);
  await page.locator("#refund-reason").fill(REASON);
  await page.locator("#refund-confirm").fill("REFUND "+TENANT);
  await expect(page.locator("#refund-submit")).toBeDisabled();
  await fill(page,fixture);await page.locator("#refund-submit").click();
  await expect(page.locator("#refund-result")).toContainText("Verified refund");
  expect(dispatches(fixture)).toHaveLength(1);
  expect(Object.keys(dispatches(fixture)[0].body).sort()).toEqual([
    "confirmation","expected_revision","idempotency_key","preview_reference","reason"]);
  expect(dispatches(fixture)[0].body.expected_revision).toBe(4);
  await expect(page.locator("#refund-submit")).toBeDisabled();
  await page.locator("#refund-receipt").click();
  await expect(page.locator("#refund-receipt-result")).toContainText("Recovered signed history");
  expect(dispatches(fixture)).toHaveLength(1);
});

test("pending refund advances only through explicit provider status refresh without another submission",async({page})=>{
  const fixture=await setup(page);fixture.state.resultStatus="pending";
  await review(page);await fill(page,fixture);await page.locator("#refund-submit").click();
  await expect(page.locator("#refund-result")).toContainText("Refund pending at Stripe");
  expect(fixture.calls.filter(r=>r.path.endsWith("/refund/refresh"))).toHaveLength(0);
  await expect(page.locator("#invoice-refund-actions button")).toBeDisabled();
  await page.locator("#refund-refresh").click();
  await expect(page.locator("#refund-receipt-result")).toContainText("Provider status refreshed");
  await expect(page.locator("#refund-receipt-result")).toContainText("Verified refund");
  expect(dispatches(fixture)).toHaveLength(1);
});

test("lost submission reply recovers signed success without resending the refund or exposing provider errors",async({page})=>{
  const fixture=await setup(page);fixture.state.loseReply=true;
  await review(page);await fill(page,fixture);await page.locator("#refund-submit").click();
  await expect(page.locator("#refund-result")).toContainText("Refund outcome unverified");
  await expect(page.locator("#refund-result")).not.toContainText("private");
  await expect(page.locator("#refund-reference")).not.toHaveValue("");
  await page.locator("#refund-receipt").click();
  await expect(page.locator("#refund-receipt-result")).toContainText("Verified refund");
  expect(dispatches(fixture)).toHaveLength(1);
});

test("explicitly disabled refunds retain review and signed history while refusing submission",async({page})=>{
  const fixture=await setup(page,{features:{billing_refund_submit:false,billing_refund_refresh:false},
    configuration:{refund_writer_enabled:false}});
  await review(page);
  await expect(page.locator("#refund-review")).toContainText("Refund submission is unavailable");
  await expect(page.locator("#refund-confirm")).toBeDisabled();
  await expect(page.locator("#refund-submit")).toBeDisabled();
  await expect(page.locator("#refund-refresh")).toBeDisabled();
  await expect(page.locator("#refund-receipt")).toBeEnabled();
  expect(dispatches(fixture)).toHaveLength(0);
});

test("missing dedicated refund setup preserves cancellation availability and signed refund history",async({page})=>{
  const fixture=await setup(page,{features:{billing_refund_preview:false,billing_refund_submit:false,
    billing_refund_refresh:false,billing_cancellation_preview:true,billing_cancellation_schedule:true},
    configuration:{refund_provider:false}});
  await page.locator("#nav-billing").click();
  await expect(page.locator("#billing-refund-capability")).toContainText("Requires the dedicated account-bound refund provider");
  await expect(page.locator("#billing-cancellation-capability")).toContainText("Available");
  await expect(page.locator("#billing-refund-history-capability")).toContainText("Available");
  await expect(page.locator("#refund-receipt")).toBeEnabled();
  await expect(page.locator("#refund-refresh")).toBeDisabled();
  await invoices(page);
  await expect(page.locator("#invoice-refund-actions button")).toHaveCount(0);
  expect(dispatches(fixture)).toHaveLength(0);
});

for(const [name,change] of [
  ["tenant",v=>v.tenant_id="another-tenant"],["amount",v=>v.amount_minor+=1],
  ["currency",v=>v.currency="cad"],["exponent",v=>v.currency_exponent=4],
  ["mode",v=>v.livemode="false"],["invoice fingerprint",v=>v.invoice_fingerprint="untrusted"],
  ["confirmation",v=>v.confirmation="REFUND ALL"],["expired",v=>v.expires_at_utc=iso(Date.now()-60000)],
  ["opaque token",v=>v.preview_reference="rk_live_private"]]) {
  test("unverified refund preview cannot authorize a submission: "+name,async({page})=>{
    const fixture=await setup(page);fixture.state.previewChange=change;
    await invoices(page);await page.locator("#invoice-refund-actions button").click();
    await expect(page.locator("#refund-review")).toContainText("No refund was submitted");
    await expect(page.locator("#refund-submit")).toBeDisabled();
    expect(dispatches(fixture)).toHaveLength(0);
  });
}

for(const [name,change] of [
  ["amount",v=>v.intent.amount_minor+=1],["mode",v=>v.intent.livemode=true],
  ["target",v=>v.intent.target_fingerprint="f".repeat(64)],["invoice",v=>v.intent.invoice_fingerprint="f".repeat(64)],
  ["unsigned",v=>v.integrity_status="unverified"],["outcome link",v=>v.outcome.intent_hash="f".repeat(64)],
  ["provider status",v=>v.outcome.provider_status="pending"]]) {
  test("corrupt signed-history projection preserves the uncertain action: "+name,async({page})=>{
    const fixture=await setup(page);fixture.state.receiptChange=change;
    await review(page);await fill(page,fixture);await page.locator("#refund-submit").click();
    await expect(page.locator("#refund-result")).toContainText("Refund outcome unverified");
    await expect(page.locator("#invoice-refund-actions button")).toBeDisabled();
    expect(dispatches(fixture)).toHaveLength(1);
  });
}

for(const [currency,amount,exponent,expected] of [["jpy",2900,0,"JPY 2,900"],
  ["kwd",12345,3,"KWD 12.345"],["usd",1000000000000,2,"USD 10,000,000,000.00"]]) {
  test("refund amount formatting preserves exact server minor units: "+currency,async({page})=>{
    const fixture=await setup(page,{currency,amount,exponent});await review(page);
    await expect(page.locator("#refund-review")).toContainText(expected+" ("+amount+" minor units)");
    expect(dispatches(fixture)).toHaveLength(0);
  });
}

test("one pending submission disables repeated dispatch and invoice selection",async({page})=>{
  const fixture=await setup(page);await review(page);await fill(page,fixture);
  const held=holdNext(fixture,"/refund");await page.locator("#refund-submit").click();await held.entered;
  await expect(page.locator("#refund-submit")).toBeDisabled();
  await expect(page.locator("#invoice-refund-actions button")).toBeDisabled();
  await expect(page.locator("#refund-refresh")).toBeDisabled();
  expect(dispatches(fixture)).toHaveLength(1);
  await release(page,held);await expect(page.locator("#refund-result")).toContainText("Verified refund");
});

test("sign-out invalidates an in-flight review and clears refund references",async({page})=>{
  const fixture=await setup(page);await invoices(page);
  const held=holdNext(fixture,"/refund/preview");await page.locator("#invoice-refund-actions button").click();await held.entered;
  await page.locator("#logout").click();await release(page,held);
  await expect(page.locator("#app")).toBeHidden();
  await expect(page.locator("#refund-confirm")).toHaveValue("");
  await expect(page.locator("#refund-reference")).toHaveValue("");
  await expect(page.locator("#refund-submit")).toBeDisabled();
  expect(dispatches(fixture)).toHaveLength(0);
});

test("invalid invoice references never become refund choices",async({page})=>{
  const fixture=await setup(page);fixture.state.invoiceChange=row=>row.invoice_reference="in_untrusted";
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:TENANT}).getByRole("button",{name:"Invoices",exact:true}).click();
  await expect(page.locator("#invoice-details")).toContainText("Invoice verification unavailable");
  await expect(page.locator("#invoice-refund-actions button")).toHaveCount(0);
  expect(fixture.calls.filter(r=>r.path.endsWith("/refund/preview"))).toHaveLength(0);
});
