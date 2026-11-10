// Authenticated Field Comparisons Save uses a revisioned tenant write.
// Cognito owner 401 must erase rejected credentials; an older owner's delayed
// 401 must not sign out the replacement session. The JSON/tenant API remains
// authoritative: these Playwright stubs simulate only Cognito identity.
const {test, expect} = require("@playwright/test");

async function signedInTenant(page, request) {
  const demo = await request.post("/v1/demo-users/demo2/key", {data: {}});
  expect(demo.status()).toBe(200);
  const summary = await request.get("/v1/applications/current", {
    headers: {"X-GuardOath-Key": (await demo.json()).api_key}
  });
  expect(summary.status()).toBe(200);
  const app = await summary.json();
  app.application_id = "usr-fc-save-revoked";
  app.tenant = {...app.tenant, tenant_id: "usr-fc-save-revoked",
    demo: false, subscription_status: "active", subscription_active: true};

  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "fc-owner-original");
  });
  const ok = body => ({status: 200, contentType: "application/json",
    body: JSON.stringify(body)});
  await page.route("**/v1/sign-in", route => route.fulfill(ok({
    authorize_url: "https://example.invalid/oauth2/authorize",
    logout_url: "https://example.invalid/logout", client_id: "synthetic",
    redirect_uri: "http://localhost/dashboard", scope: "openid email",
    registration_enabled: true, google_provider: null
  })));
  await page.route("**/v1/account/status", route => route.fulfill(ok({
    registered: true, registration_enabled: true,
    tenant_id: "usr-fc-save-revoked", subscription_status: "active",
    can_validate: true
  })));
  await page.route("**/v1/applications/current", route => route.fulfill(ok(app)));
  await page.route("**/v1/tenants/current/statistical-drift", route =>
    route.fulfill(ok({revision: app.tenant.revision, rules: []})));
  await page.route("**/v1/tenants/current/keys", route =>
    route.fulfill(ok({keys: [], max_keys: 2})));
  await page.goto("/dashboard");
  await expect(page.locator("#tenant-panel")).toBeVisible();
  await expect(page.locator("#keys-panel")).toBeVisible();
}

async function stageComparisonEdit(page) {
  const select = page.locator("#tenant-check-input-cross_field");
  await expect(select).toBeVisible();
  const value = await select.inputValue();
  await select.selectOption(value === "block" ? "flag" : "block");
  await expect(page.locator("#tenant-message")).toContainText("Unsaved changes");
}

test("revoked Cognito owner FC Save 401 clears tenant, key UI and session", async ({page, request}) => {
  await signedInTenant(page, request);
  await stageComparisonEdit(page);
  let saves = 0;
  await page.route("**/v1/tenants/current/checks", route => {
    if (route.request().method() !== "POST") return route.continue();
    saves++;
    expect(route.request().headers()["x-guardoath-session"]).toBe("fc-owner-original");
    expect(route.request().headers()["x-guardoath-key"]).toBeUndefined();
    expect(JSON.parse(route.request().postData()).changes).toHaveProperty(
      "input_check_cross_field");
    return route.fulfill({status: 401, contentType: "application/json",
      body: JSON.stringify({detail: "Unauthorized"})});
  });
  await page.getByRole("button", {name: "Save checks", exact: true}).click();
  await expect(page.locator("#account-status")).toContainText("expired or been revoked");
  await expect(page.locator("#sign-in")).toBeVisible();
  await expect(page.locator("#sign-out")).toBeHidden();
  await expect(page.locator("#tenant-panel")).toBeHidden();
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#key-new")).toHaveValue("");
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session"))).toBeNull();
  expect(saves).toBe(1);
  await expect(page.locator("#tenant-message")).not.toContainText("Saved.");
});

test("old FC Save 401 cannot revoke a new Cognito owner session", async ({page, request}) => {
  await signedInTenant(page, request);
  await stageComparisonEdit(page);
  let release, notify;
  const pending = new Promise(resolve => { notify = resolve; });
  const held = new Promise(resolve => { release = resolve; });
  await page.route("**/v1/tenants/current/checks", async route => {
    if (route.request().method() !== "POST") return route.continue();
    notify();
    await held;
    await route.fulfill({status: 401, contentType: "application/json",
      body: JSON.stringify({detail: "Revoked old owner"})});
  });
  // Capture actual click-handler promise, not a timeout-based guess.
  await page.evaluate(() => {
    const save = saveTenant;
    saveTenant = (...args) => {
      window.__fcSaveOutcome = save(...args);
      return window.__fcSaveOutcome;
    };
  });
  try {
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await pending;
    await page.evaluate(() => {
      sessionStorage.setItem("guardoath-session", "fc-owner-replacement");
      document.getElementById("account-status").textContent = "Replacement owner";
    });
    release();
    expect(await page.evaluate(() => window.__fcSaveOutcome)).toBe(false);
    expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session")))
      .toBe("fc-owner-replacement");
    await expect(page.locator("#account-status")).toHaveText("Replacement owner");
    await expect(page.locator("#tenant-message")).not.toContainText("Saved.");
  } finally {
    release?.();
  }
});
