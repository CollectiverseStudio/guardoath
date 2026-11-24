"use strict";

const {test, expect} = require("@playwright/test");
const {installAdminFixture, openFixtureSession} = require("./admin_fixture");
const CURSOR = "ic1." + "a".repeat(80);
const PATH = "/v1/admin/subscriptions/sample-studio/invoices";
const json = (body, status=200) => ({status, contentType:"application/json", body:JSON.stringify(body)});

async function finishHeldInvoice(page, release, done) {
  const delivered = page.waitForResponse(response=>{
    const url = new URL(response.url());
    return url.pathname === PATH && url.searchParams.has("cursor");
  });
  release();
  const response = await delivered;
  expect(await response.finished()).toBeNull();
  await done;
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
}

function report(tenant, pageNumber=1, status=null) {
  const make = (state, amount, currency="usd") => ({status:state, currency,
    created_utc:"2026-11-20T12:00:00Z", amount_due_minor:amount,
    amount_paid_minor:state === "paid" ? amount : 0,
    amount_remaining_minor:state === "paid" ? 0 : amount,
    collection_method:"charge_automatically", attempted:true, attempt_count:1,
    next_payment_attempt_utc:null});
  const all = [...Array.from({length:5},(_,i)=>make("paid",100+i)),
    make("open",700), make("uncollectible",900,"eur")];
  const selected = status === null ? all : all.filter(row=>row.status===status);
  const rows = selected.slice((pageNumber-1)*5,pageNumber*5);
  const hasMore = selected.length > pageNumber*5;
  const open = rows.filter(row=>row.status==="open"&&row.amount_remaining_minor>0);
  const balances = {};
  for (const row of open) balances[row.currency]=(balances[row.currency]||0)+row.amount_remaining_minor;
  return {tenant_id:tenant, read_audit_event_id:"a".repeat(64), history:{
    verified:true,status:"verified",invoices:rows,count:rows.length,has_more:hasMore,
    page_number:pageNumber,status_filter:status,next_cursor:hasMore?CURSOR:null,
    read_at_utc:"2026-11-23T12:00:00Z",payment_review:{
      scope:pageNumber===1&&status===null?"verified_first_page_only":"verified_selected_page_only",
      open_with_balance_count:open.length,open_with_recorded_next_attempt_count:0,
      uncollectible_count:rows.filter(row=>row.status==="uncollectible").length,
      open_balance_by_currency_minor:balances,
      complete_history:pageNumber===1&&status===null&&!hasMore}}};
}

async function setup(page, options={}) {
  const state = {mutate:value=>value, status:200};
  const fixture = await installAdminFixture(page,{...options,override:request=>{
    if (!/^\/v1\/admin\/subscriptions\/[^/]+\/invoices$/.test(request.path)) return null;
    const value = report(request.path.split("/")[4],request.url.searchParams.has("cursor")?2:1,
      request.url.searchParams.get("status"));
    return json(state.mutate(value,request),state.status);
  }});
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  const choose = async (tenant="sample-studio",action="Invoices")=>{
    await page.locator("#nav-tenants").click();
    await page.locator("#tenant-rows tr").filter({hasText:tenant})
      .getByRole("button",{name:action,exact:true}).click();
  };
  await choose();
  await expect(page.locator("#invoice-page-status")).toContainText("Page 1");
  return {fixture,state,choose};
}

test("owner reaches older open invoices and filters status without summing separate pages",async ({page})=>{
  const {fixture} = await setup(page);
  await expect(page.locator("#invoice-details")).toContainText("paid USD due 100");
  await page.locator("#invoice-next").click();
  await expect(page.locator("#invoice-page-status")).toContainText("Page 2");
  await expect(page.locator("#invoice-details")).toContainText("open USD due 700");
  await expect(page.locator("#invoice-details")).toContainText("uncollectible EUR due 900");
  await expect(page.locator("#invoice-details")).toContainText("Shown page only; not complete invoice history");
  await expect(page.locator("#invoice-details")).not.toContainText("paid USD due 100");
  await expect(page.locator("#invoice-next")).toBeDisabled();
  await page.locator("#invoice-status-filter").selectOption("open");
  await expect(page.locator("#invoice-page-status")).toContainText("Page 1 · open");
  await expect(page.locator("#invoice-details")).toContainText("Open remaining by currency: USD 700 minor units");
  await expect(page.locator("#invoice-details")).not.toContainText("EUR 900");
  await page.locator("#invoice-status-filter").selectOption("draft");
  await expect(page.locator("#invoice-details")).toContainText("No invoices in provider page");
  const calls = fixture.calls.filter(call=>call.path===PATH);
  expect(calls.map(call=>[call.url.searchParams.get("cursor"),call.url.searchParams.get("status")]))
    .toEqual([[null,null],[CURSOR,null],[null,"open"],[null,"draft"]]);
  expect(calls.every(call=>call.method==="GET"&&call.headers["x-guardoath-admin-session"]===fixture.token&&
    call.headers["x-guardoath-key"]===undefined)).toBe(true);
  expect(await page.evaluate(()=>JSON.stringify([...Object.entries(sessionStorage),...Object.entries(localStorage)])))
    .not.toContain(CURSOR);
});

for (const [name,mutate] of [
  ["wrong page",value=>{value.history.page_number=1;}],
  ["missing page",value=>{delete value.history.page_number;}],
  ["wrong filter",value=>{value.history.status_filter="open";}],
  ["raw invoice ID",value=>{value.history.has_more=true;value.history.next_cursor="in_private";}],
  ["provider URL",value=>{value.history.has_more=true;value.history.next_cursor="https://private.invalid";}],
  ["more without cursor",value=>{value.history.has_more=true;}],
  ["cursor without more",value=>{value.history.next_cursor="ic1."+"b".repeat(80);}],
  ["repeated cursor",value=>{value.history.has_more=true;value.history.next_cursor=CURSOR;}],
  ["empty nonadvancing page",value=>{value.history.invoices=[];value.history.count=0;
    value.history.has_more=true;value.history.next_cursor="ic1."+"b".repeat(80);}],
  ["false complete history",value=>{value.history.payment_review.complete_history=true;}],
  ["first page review scope",value=>{value.history.payment_review.scope="verified_first_page_only";}],
  ["cross tenant",value=>{value.tenant_id="sample-other";}],
  ["cross currency balance",value=>{value.history.payment_review.open_balance_by_currency_minor={eur:700};}],
]) {
  test("owner refuses continuation with "+name,async ({page})=>{
    const {state} = await setup(page);
    state.mutate=value=>{mutate(value);return value;};
    await page.locator("#invoice-next").click();
    await expect(page.locator("#invoice-details")).toContainText("Invoice verification unavailable; no billing changes");
    await expect(page.locator("#invoice-details")).not.toContainText("open USD due 700");
    await expect(page.locator("#invoice-next")).toBeDisabled();
    await expect(page.locator("#invoice-restart")).toBeEnabled();
  });
}

test("filtered invoice rows must match the selected provider status",async ({page})=>{
  const {state} = await setup(page);
  state.mutate=value=>{value.history.invoices[0].status="paid";return value;};
  await page.locator("#invoice-status-filter").selectOption("open");
  await expect(page.locator("#invoice-details")).toContainText("Invoice verification unavailable; no billing changes");
  await expect(page.locator("#invoice-next")).toBeDisabled();
});

for (const status of [400,429,503]) {
  test("invoice failure "+status+" clears continuation and supports a fresh restart",async ({page})=>{
    const {state,fixture} = await setup(page);
    state.status=status;
    state.mutate=()=>({detail:"private-provider-body-never-render"});
    await page.locator("#invoice-next").click();
    await expect(page.locator("#invoice-details")).toContainText("Invoice verification unavailable");
    await expect(page.locator("#invoice-page-status")).toContainText(status===400?"expired":status===429?"limit reached":"unavailable");
    await expect(page.locator("#app")).not.toContainText("private-provider-body");
    await expect(page.locator("#invoice-next")).toBeDisabled();
    state.status=200;state.mutate=value=>value;
    await page.locator("#invoice-restart").click();
    await expect(page.locator("#invoice-page-status")).toContainText("Page 1");
    expect(fixture.calls.filter(call=>call.path===PATH).at(-1).url.searchParams.has("cursor")).toBe(false);
  });
}

test("one invoice continuation stays in flight despite repeated click events",async ({page})=>{
  await setup(page);
  let release, started, finished;
  const held = new Promise(resolve=>{release=resolve;});
  const begun = new Promise(resolve=>{started=resolve;});
  const done = new Promise(resolve=>{finished=resolve;});
  let count=0;
  await page.route("**/v1/admin/subscriptions/sample-studio/invoices?*",async route=>{
    count++;started();await held;await route.fulfill(json(report("sample-studio",2)));finished();
  });
  await page.locator("#invoice-next").click();await begun;
  await expect(page.locator("#invoice-next")).toBeDisabled();
  await expect(page.locator("#invoice-restart")).toBeDisabled();
  await expect(page.locator("#invoice-status-filter")).toBeDisabled();
  await page.locator("#invoice-next").dispatchEvent("click");
  expect(count).toBe(1);
  release();await done;
  await expect(page.locator("#invoice-page-status")).toContainText("Page 2");
  expect(count).toBe(1);
});

for (const outcome of ["success","failure"]) for (const action of ["other tenant","subscription","refresh","logout"]) {
  test("late invoice "+outcome+" cannot repaint after "+action,async ({page})=>{
    const {choose} = await setup(page);
    let release, started, finished;
    const held = new Promise(resolve=>{release=resolve;});
    const begun = new Promise(resolve=>{started=resolve;});
    const done = new Promise(resolve=>{finished=resolve;});
    await page.route("**/v1/admin/subscriptions/sample-studio/invoices?*",async route=>{
      started();await held;
      await route.fulfill(outcome==="success"?json(report("sample-studio",2)):json({detail:"private old failure"},503));
      finished();
    });
    await page.locator("#invoice-next").click();await begun;
    if (action==="other tenant") {
      await choose("sample-labs");
      await expect(page.locator("#invoice-details")).toContainText("sample-labs");
    } else if (action==="subscription") {
      await choose("sample-labs","Verify Stripe");
      await expect(page.locator("#billing-details")).toContainText("sample-labs");
    } else if (action==="refresh") {
      await page.locator("#refresh").click();
      await expect(page.locator("#status")).toContainText("Administration loaded");
    } else {
      await page.locator("#logout").click();
      await expect(page.locator("#app")).toBeHidden();
    }
    const before = await page.locator("#invoice-details").textContent();
    const pageBefore = await page.locator("#invoice-page-status").textContent();
    await finishHeldInvoice(page,release,done);
    expect(await page.locator("#invoice-details").textContent()).toBe(before);
    expect(await page.locator("#invoice-page-status").textContent()).toBe(pageBefore);
    if (action!=="other tenant") await expect(page.locator("#invoice-next")).toBeDisabled();
  });
}

test("invoice controls fit the existing mobile billing workspace",async ({page})=>{
  await page.setViewportSize({width:390,height:844});
  await setup(page);
  await page.locator("#invoice-next").click();
  await expect(page.locator("#invoice-page-status")).toContainText("Page 2");
  expect(await page.evaluate(()=>document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
});

for (const status of [401,403]) {
  test(`old-session invoice ${status} cannot sign out a newly authenticated owner`,async ({page})=>{
    await setup(page,{nativeMfa:true});
    let release, started, finished;
    const held = new Promise(resolve=>{release=resolve;});
    const begun = new Promise(resolve=>{started=resolve;});
    const done = new Promise(resolve=>{finished=resolve;});
    await page.route("**/v1/admin/subscriptions/sample-studio/invoices?*",async route=>{
      started();await held;await route.fulfill(json({detail:"old-session-expired"},status));finished();
    });
    await page.locator("#invoice-next").click();await begun;
    await page.locator("#logout").click();
    await page.locator("#native-email").fill("owner@example.invalid");
    await page.locator("#native-password").fill("synthetic-test-password-only");
    await page.locator("#native-start").click();
    await expect(page.locator("#native-challenge")).toBeVisible();
    await page.locator("#native-code").fill("123456");
    await page.locator("#native-finish").click();
    await expect(page.locator("#app")).toBeVisible();
    await expect(page.locator("#status")).toContainText("Administration loaded");
    await finishHeldInvoice(page,release,done);
    await expect(page.locator("#app")).toBeVisible();
    await expect(page.locator("#status")).toContainText("Administration loaded");
    await expect(page.locator("#invoice-page-status")).toBeEmpty();
    await expect(page.locator("#invoice-next")).toBeDisabled();
  });
}

test("same tenant row cannot start a duplicate in-flight invoice read",async ({page})=>{
  const {choose} = await setup(page);
  let release, started, finished;
  const held = new Promise(resolve=>{release=resolve;});
  const begun = new Promise(resolve=>{started=resolve;});
  const done = new Promise(resolve=>{finished=resolve;});
  let calls=0;
  await page.route("**/v1/admin/subscriptions/sample-studio/invoices*",async route=>{
    calls++;started();await held;await route.fulfill(json(report("sample-studio",2)));finished();
  });
  await page.locator("#invoice-next").click();await begun;
  await choose();
  expect(calls).toBe(1);
  release();await done;
  await expect(page.locator("#invoice-page-status")).toContainText("Page 2");
  expect(calls).toBe(1);
});

for (const outcome of ["success","failure"]) {
  test("confirmed downgrade clears old invoice state before late "+outcome,async ({page})=>{
    const {fixture,choose} = await setup(page,{features:{billing_downgrade:true}});
    const prior=fixture.override;
    fixture.override=(request,f)=>request.path.endsWith("/downgrade")
      ? json({detail:"Synthetic current subscription conflict"},409) : prior(request,f);
    let release, started, finished;
    const held=new Promise(resolve=>{release=resolve;});
    const begun=new Promise(resolve=>{started=resolve;});
    const done=new Promise(resolve=>{finished=resolve;});
    await page.route("**/v1/admin/subscriptions/sample-studio/invoices?*",async route=>{
      started();await held;
      await route.fulfill(outcome==="success"?json(report("sample-studio",2)):json({detail:"old failure"},503));
      finished();
    });
    await page.locator("#invoice-next").click();await begun;
    const answers=["RECONCILE sample-studio","Inspect synthetic unpaid subscription status"];
    page.on("dialog",async dialog=>dialog.type()==="confirm"?dialog.accept():dialog.accept(answers.shift()));
    await choose("sample-studio","Reconcile unpaid");
    await expect(page.locator("#status")).toContainText("Request failed (409)");
    expect(answers).toEqual([]);
    const before=await page.locator("#invoice-details").textContent();
    await finishHeldInvoice(page,release,done);
    expect(await page.locator("#invoice-details").textContent()).toBe(before);
    await expect(page.locator("#invoice-details")).not.toContainText("open USD due 700");
    await expect(page.locator("#invoice-next")).toBeDisabled();
    await expect(page.locator("#invoice-restart")).toBeDisabled();
  });
}
