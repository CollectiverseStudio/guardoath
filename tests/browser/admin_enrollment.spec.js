"use strict";

const {test, expect} = require("@playwright/test");
const {installAdminFixture} = require("./admin_fixture");

// Execute the real product HTML/JS with isolated synthetic endpoints only.
const SEED = "A".repeat(32);
const PASSWORD = "synthetic-password-for-browser-test";
const privateCalls = fixture => fixture.calls.filter(c => c.path.startsWith("/v1/admin/") && !c.path.startsWith("/v1/admin/auth/"));

async function loginPage(page, options = {}) {
  await page.addInitScript(() => Object.defineProperty(navigator, "clipboard", {
    value:{writeText:async value => {window.syntheticCopiedKey = value;}}, configurable:true
  }));
  const fixture = await installAdminFixture(page, {nativeMfa:true,nativeEnrollment:true,...options});
  await page.goto("/admin/login");
  await expect(page.locator("#native-auth")).toBeVisible();
  return fixture;
}

async function begin(page) {
  await page.locator("#native-email").fill("owner@example.invalid");
  await page.locator("#native-password").fill(PASSWORD);
  await page.locator("#enrollment-start").click();
  await expect(page.locator("#enrollment-key")).toHaveValue(SEED);
  await expect(page.locator("#native-password")).toHaveValue("");
}

async function cleared(page) {
  await expect(page.locator("#enrollment-setup")).toBeHidden();
  for (const id of ["enrollment-key","enrollment-code","native-password"])
    await expect(page.locator("#" + id)).toHaveValue("");
  const storage = await page.evaluate(() => Object.values(localStorage).concat(Object.values(sessionStorage)).join(" "));
  for (const sensitive of [SEED,PASSWORD,"synthetic-enrollment-ticket"]) expect(storage).not.toContain(sensitive);
}

test("enrollment capability is hidden without explicit independent storage wiring", async ({page}) => {
  const fixture = await loginPage(page, {nativeEnrollment:false});
  await expect(page.locator("#enrollment-entry")).toBeHidden();
  await expect(page.locator("#enrollment-unavailable")).toContainText("Ask the operator to authorize");
  await expect(page.locator("#enrollment-setup")).toBeHidden();
  expect(fixture.calls.filter(c => c.path.includes("/enrollment/")).length).toBe(0);
});

test("first authenticator setup is visible, masked, copied explicitly and requires a fresh MFA login", async ({page}) => {
  const fixture = await loginPage(page);
  await begin(page);
  await expect(page.locator("#enrollment-key")).toHaveAttribute("type","password");
  await expect(page.locator("#enrollment-key")).toHaveAttribute("readonly", "");
  await expect(page.locator("#enrollment-message")).toContainText("not signed in yet");
  await page.locator("#enrollment-copy").click();
  expect(await page.evaluate(() => window.syntheticCopiedKey)).toBe(SEED);
  await expect(page.locator("#enrollment-message")).toContainText("clear it from your clipboard");
  await page.locator("#enrollment-code").fill("123456");
  await page.locator("#enrollment-finish").click();
  await expect(page.locator("#login-message")).toContainText("Authenticator saved");
  await cleared(page);
  await expect(page.locator("#app")).toBeHidden();
  expect(privateCalls(fixture)).toHaveLength(0);
  for (const call of fixture.calls.filter(c => c.path.includes("/enrollment/")))
    expect(call.headers["x-guardoath-admin-session"]).toBeUndefined();
  await page.locator("#native-password").fill(PASSWORD);
  await page.locator("#native-start").click();
  await expect(page.locator("#native-challenge")).toBeVisible();
  await page.locator("#native-code").fill("123456");
  await page.locator("#native-finish").click();
  await expect(page.locator("#app")).toBeVisible();
  await page.locator("#logout").click();
  await cleared(page);
  await expect(page.locator("#app")).toBeHidden();
});

test("wrong code stays pending for a bounded retry and clears the entered code", async ({page}) => {
  const fixture = await loginPage(page);
  await begin(page);
  await page.locator("#enrollment-code").fill("000000");
  await page.locator("#enrollment-finish").click();
  await expect(page.locator("#enrollment-message")).toContainText("code was not accepted");
  await expect(page.locator("#enrollment-code")).toHaveValue("");
  await expect(page.locator("#enrollment-key")).toHaveValue(SEED);
  await page.locator("#enrollment-code").fill("123456");
  await page.locator("#enrollment-finish").click();
  await cleared(page);
  const finishes = fixture.calls.filter(c => c.path.endsWith("/enrollment/finish"));
  expect(finishes).toHaveLength(2);
  expect(finishes[0].body.ticket).toBe(finishes[1].body.ticket);
  expect(privateCalls(fixture)).toHaveLength(0);
});

test("provider failure never claims enrollment success or leaves key and ticket usable", async ({page}) => {
  const fixture = await loginPage(page, {override: request => request.path.endsWith("/enrollment/finish")
    ? {status:503,contentType:"application/json",body:JSON.stringify({detail:"private-provider-message"})} : null});
  await begin(page);
  await page.locator("#enrollment-code").fill("123456");
  await page.locator("#enrollment-finish").click();
  await expect(page.locator("#login-message")).toContainText("could not be confirmed");
  await expect(page.locator("body")).not.toContainText("private-provider-message");
  await expect(page.locator("#login-message")).not.toContainText("saved");
  await cleared(page);
  await page.locator("#enrollment-finish").dispatchEvent("click");
  expect(fixture.calls.filter(c => c.path.endsWith("/enrollment/finish"))).toHaveLength(1);
  expect(privateCalls(fixture)).toHaveLength(0);
});

test("denied first-factor setup clears the password and does not reveal a key", async ({page}) => {
  const fixture = await loginPage(page, {override: request => request.path.endsWith("/enrollment/start")
    ? {status:401,contentType:"application/json",body:'{"detail":"not authorized"}'} : null});
  await page.locator("#native-email").fill("owner@example.invalid");
  await page.locator("#native-password").fill(PASSWORD);
  await page.locator("#enrollment-start").click();
  await expect(page.locator("#login-message")).toContainText("not authorized");
  await cleared(page);
  expect(privateCalls(fixture)).toHaveLength(0);
});

test("cancel clears all setup fields immediately and cancels only its pending ticket", async ({page}) => {
  const fixture = await loginPage(page);
  await begin(page);
  await page.locator("#enrollment-code").fill("123456");
  await page.locator("#enrollment-cancel").click();
  await cleared(page);
  await expect.poll(() => fixture.calls.filter(c => c.path.endsWith("/enrollment/cancel")).length).toBe(1);
  await page.locator("#enrollment-finish").dispatchEvent("click");
  expect(fixture.calls.filter(c => c.path.endsWith("/enrollment/finish"))).toHaveLength(0);
  await expect(page.locator("#native-start")).toBeEnabled();
});

test("expiry clears the key, OTP and ticket without signing in", async ({page}) => {
  await page.clock.install();
  const fixture = await loginPage(page);
  await begin(page);
  await page.locator("#enrollment-code").fill("123456");
  await page.clock.fastForward(180001);
  await expect(page.locator("#login-message")).toContainText("expired");
  await cleared(page);
  expect(privateCalls(fixture)).toHaveLength(0);
});

test("a late setup response after cancellation cannot restore the key", async ({page}) => {
  const fixture = await loginPage(page);
  let release, started = false;
  const held = new Promise(resolve => {release = resolve;});
  await page.route("**/v1/admin/auth/enrollment/start", async route => {
    const r = route.request();
    const response = fixture.resolve(r.url(), r.method(), r.headers(), r.postData());
    started = true;
    await held;
    await route.fulfill(response);
  });
  await page.locator("#native-email").fill("owner@example.invalid");
  await page.locator("#native-password").fill(PASSWORD);
  await page.locator("#enrollment-start").click();
  await expect.poll(() => started).toBe(true);
  await page.locator("#enrollment-cancel").click();
  await cleared(page);
  release();
  await expect.poll(() => fixture.calls.filter(c => c.path.endsWith("/enrollment/cancel")).length).toBe(1);
  await cleared(page);
  expect(privateCalls(fixture)).toHaveLength(0);
});

test("page exit and sign-out handlers clear transient setup state", async ({page}) => {
  await loginPage(page);
  await begin(page);
  await page.evaluate(() => window.dispatchEvent(new Event("pagehide")));
  await cleared(page);
  await begin(page);
  // The same logout handler also clears any pending setup during a forced sign-out.
  await page.locator("#logout").dispatchEvent("click");
  await cleared(page);
});

test("setup controls fit a narrow viewport without horizontal scrolling", async ({page}) => {
  await page.setViewportSize({width:390,height:844});
  await loginPage(page);
  await begin(page);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator("#enrollment-key")).toBeVisible();
  await expect(page.locator("#enrollment-finish")).toBeVisible();
  await expect(page.locator("#enrollment-cancel")).toBeVisible();
});
