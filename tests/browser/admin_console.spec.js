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
