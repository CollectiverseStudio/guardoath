"use strict";
const {test,expect} = require("@playwright/test");
const {installAdminFixture,openFixtureSession} = require("./admin_fixture");

// Focused failure/race tests for the real browser client. The independent
// admin_integration suite covers actual mounted APIs and signed persistence.
const EVENT = "b".repeat(64);
const json = (body,status=200) => ({status,contentType:"application/json",body:JSON.stringify(body)});

async function setup(page,options={}) {
  const state = {posts:[],receipt:null,outcome:null,reject:null,lostResponse:false,
    badOutcome:null,badReceipt:null,refreshFails:false};
  const fixture = await installAdminFixture(page,{features:{billing_reconcile:true},
    override:(request,fixture) => {
      if (request.path === "/v1/admin/subscriptions/sample-studio/reconcile" && request.method === "POST") {
        state.posts.push(request.body);
        if (state.reject) return json({detail:"Synthetic definitive rejection"},state.reject);
        const replayed = state.outcome !== null;
        if (!state.outcome) {
          const tenant = fixture.tenants[0];
          state.outcome = {tenant_id:tenant.tenant_id,revision:tenant.revision+1,subscription_status:"active",
            provider_status:"active",monthly_allowance:tenant.monthly_allowance,overage:"block",
            plan_monthly_allowance:10000,admin_monthly_allowance_cap:options.cap ?? null,
            audit_event_id:EVENT,replayed:false};
          state.receipt = {event_id:EVENT,status:"ok",integrity_status:"signed_verified",
            operation:"billing.reconcile",tenant_id:tenant.tenant_id,prior_revision:tenant.revision,
            new_revision:tenant.revision+1,change:{field:"subscription_status",before:tenant.subscription_status,
              after:"active",provider_status:"active",allowance_before:tenant.monthly_allowance,
              allowance_after:tenant.monthly_allowance,plan_allowance:10000,operator_cap:options.cap ?? null}};
          tenant.revision += 1;
        }
        if (state.lostResponse) {state.lostResponse=false;return json({detail:"Synthetic committed response loss"},503);}
        const result = {...state.outcome,replayed};
        return json(state.badOutcome ? state.badOutcome(result) : result);
      }
      if (request.path === "/v1/admin/audit/" + EVENT && state.receipt)
        return json({audit:state.badReceipt ? state.badReceipt(state.receipt) : state.receipt});
      if (request.path === "/v1/admin/catalog" && state.outcome && state.refreshFails)
        return json({detail:"Synthetic refresh outage"},503);
      return null;
    }});
  if (options.cap !== undefined) fixture.tenants[0].monthly_allowance=options.cap;
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  const answers = [];
  page.on("dialog",async dialog => {
    if (dialog.type() === "confirm") await dialog.accept();
    else {expect(answers.length).toBeGreaterThan(0);await dialog.accept(answers.shift());}
  });
  async function start() {
    await page.locator("#nav-tenants").click();
    answers.push("RECONCILE sample-studio","Verify synthetic subscription reconciliation");
    await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
      .getByRole("button",{name:"Reconcile subscription",exact:true}).click();
  }
  return {state,fixture,start};
}

test("billing UI requires signed receipt and reports a preserved request cap",async ({page}) => {
  const {state,start} = await setup(page,{cap:4000});
  await start();
  await expect(page.locator("#status")).toContainText("Preserved request cap: 4000");
  await expect(page.locator("#billing-retry")).toBeHidden();
  expect(Object.keys(state.posts[0]).sort()).toEqual(["confirmation","expected_revision","idempotency_key","reason"]);
  expect(state.posts[0].expected_revision).toBe(4);
});

test("committed-response loss retries the identical action reference and recovers its receipt",async ({page}) => {
  const {state,start} = await setup(page);
  state.lostResponse=true;
  await start();
  await expect(page.locator("#billing-action-result")).toContainText("Reconciliation not verified");
  await expect(page.locator("#billing-retry")).toBeEnabled();
  await page.locator("#billing-retry").click();
  await expect(page.locator("#status")).toContainText("Prior reconciliation receipt recovered");
  expect(state.posts).toHaveLength(2);
  expect(state.posts[1]).toEqual(state.posts[0]);
});

for (const corrupt of ["response","receipt"]) {
  test(`malformed ${corrupt} cannot produce a verified billing success`,async ({page}) => {
    const {state,start} = await setup(page);
    if (corrupt === "response") state.badOutcome = value=>({...value,monthly_allowance:50000});
    else state.badReceipt = value=>({...value,change:{...value.change,operator_cap:1}});
    await start();
    await expect(page.locator("#billing-action-result")).toContainText("Reconciliation not verified");
    await expect(page.locator("#billing-action-result")).not.toContainText("Subscription reconciled with Stripe");
    await expect(page.locator("#billing-retry")).toBeEnabled();
    state.badOutcome=null;state.badReceipt=null;
    await page.locator("#billing-retry").click();
    await expect(page.locator("#status")).toContainText("Prior reconciliation receipt recovered");
    expect(state.posts[1]).toEqual(state.posts[0]);
  });
}

for (const status of [409,422]) {
  test(`definitive ${status} rejection permits a reviewed new action`,async ({page}) => {
    const {state,start} = await setup(page);
    state.reject=status;
    await start();
    await expect(page.locator("#billing-action-result")).toContainText("Reconciliation rejected");
    await expect(page.locator("#billing-retry")).toBeHidden();
    state.reject=null;
    await page.locator("#refresh").click();
    await expect(page.locator("#status")).toContainText("Administration loaded");
    await start();
    await expect(page.locator("#status")).toContainText("Subscription reconciled with Stripe");
    expect(state.posts).toHaveLength(2);
    expect(state.posts[0].idempotency_key).not.toBe(state.posts[1].idempotency_key);
  });
}

test("failed refresh after signed reconciliation does not erase the verified outcome or enable a dead retry",async ({page}) => {
  const {state,start} = await setup(page);
  state.refreshFails=true;
  await start();
  await expect(page.locator("#billing-action-result")).toContainText("Subscription reconciled with Stripe");
  await expect(page.locator("#billing-action-result")).toContainText("Workspace refresh unavailable");
  await expect(page.locator("#billing-action-result")).not.toContainText("Reconciliation not verified");
  await expect(page.locator("#billing-retry")).toBeHidden();
  expect(state.posts).toHaveLength(1);
});

test("a late reconciliation response after sign-out cannot repopulate owner data",async ({page}) => {
  const {start} = await setup(page);
  let release,started;
  const held = new Promise(resolve=>release=resolve);
  const observed = new Promise(resolve=>started=resolve);
  const delivered = page.waitForResponse(response=>response.url().endsWith("/v1/admin/subscriptions/sample-studio/reconcile"));
  await page.route("**/v1/admin/subscriptions/sample-studio/reconcile",async route=>{
    started();await held;await route.fallback();
  });
  await start();
  await observed;
  await page.locator("#logout").click();
  release();
  await (await delivered).finished();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>resolve())));
  await expect(page.locator("#app")).toBeHidden();
  await expect(page.locator("#tenant-rows tr")).toHaveCount(0);
  await expect(page.locator("#billing-action-result")).toBeEmpty();
  await expect(page.locator("#billing-retry")).toBeHidden();
});
