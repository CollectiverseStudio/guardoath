const {test, expect} = require("@playwright/test");

// Real dashboard, real JSON-response suspension; synthetic Cognito owner only.
// Python API tests separately enforce paid entitlement and key ownership.
async function owner(page, request, {keyListStatus = 200, keyMutationStatus = 200} = {}) {
  const demo = await request.post("/v1/demo-users/demo2/key", {data: {}});
  expect(demo.status()).toBe(200);
  const summary = await request.get("/v1/applications/current", {
    headers: {"X-GuardOath-Key": (await demo.json()).api_key}
  });
  expect(summary.status()).toBe(200);
  const app = await summary.json();
  app.application_id = "usr-key-json-race";
  app.tenant = {...app.tenant, tenant_id: "usr-key-json-race", demo: false,
    subscription_status: "active", subscription_active: true};
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "old-owner-session");
    const original = Response.prototype.json;
    Response.prototype.json = async function(...args) {
      const value = await original.apply(this, args);
      if (this.url.includes("/v1/tenants/current/keys") && window.__holdKeyBody) {
        window.__holdKeyBody = false;
        await new Promise(resolve => { window.__releaseKeyBody = resolve; });
      }
      return value;
    };
  });
  const ok = body => ({status: 200, contentType: "application/json", body: JSON.stringify(body)});
  await page.route("**/v1/sign-in", route => route.fulfill(ok({
    authorize_url: "https://example.invalid/oauth2/authorize",
    logout_url: "https://example.invalid/logout", client_id: "synthetic",
    redirect_uri: "http://localhost/dashboard", scope: "openid email",
    registration_enabled: true, google_provider: null
  })));
  await page.route("**/v1/account/status", route => route.fulfill(ok({
    registered: true, registration_enabled: true, tenant_id: "usr-key-json-race",
    subscription_status: "active", can_validate: true
  })));
  await page.route("**/v1/applications/current", route => route.fulfill(ok(app)));
  await page.route("**/v1/tenants/current/statistical-drift", route => route.fulfill(ok({
    revision: app.tenant.revision, rules: []
  })));
  await page.route("**/v1/tenants/current/keys", route => {
    expect(route.request().headers()["x-guardoath-session"]).toBe("old-owner-session");
    const mutation = route.request().method() === "POST";
    return route.fulfill({...ok(mutation
      ? {api_key: "gok_synthetic_old_owner_only"}
      : {keys: [{key_id: "old-owner-key"}], max_keys: 2}),
      status: mutation ? keyMutationStatus : keyListStatus});
  });
  await page.goto("/dashboard");
  if (keyListStatus === 401) {
    await expect(page.locator("#account-status")).toContainText("expired or been revoked");
    return;
  }
  await expect(page.locator("#keys-panel")).toBeVisible();
  await expect(page.locator("#key-create")).toBeEnabled();
}

test("old API-key list JSON cannot overwrite a replacement customer's key view", async ({page, request}) => {
  await owner(page, request);
  await expect(page.locator("#key-list")).toContainText("old-owner-key");
  await page.evaluate(() => { window.__holdKeyBody = true; void loadKeys(); });
  await page.waitForFunction(() => typeof window.__releaseKeyBody === "function");
  await page.evaluate(() => {
    sessionStorage.setItem("guardoath-session", "replacement-owner");
    document.querySelector("#key-list").textContent = "NEW OWNER VIEW";
    window.__releaseKeyBody();
  });
  await page.waitForTimeout(75);
  await expect(page.locator("#key-list")).toHaveText("NEW OWNER VIEW");
});

test("old API-key creation JSON cannot display a previous owner's one-time secret", async ({page, request}) => {
  await owner(page, request);
  await page.evaluate(() => { window.__holdKeyBody = true; });
  await page.locator("#key-create").click();
  await page.waitForFunction(() => typeof window.__releaseKeyBody === "function");
  await page.evaluate(() => {
    sessionStorage.setItem("guardoath-session", "replacement-owner");
    window.__releaseKeyBody();
  });
  await page.waitForTimeout(75);
  await expect(page.locator("#key-new")).toHaveValue("");
  await expect(page.locator("#key-message")).not.toContainText("New API key, shown once");
});


test("revoked owner key-list 401 clears Cognito session, tenant and key controls", async ({page, request}) => {
  await owner(page, request, {keyListStatus: 401});
  await expect(page.locator("#sign-in")).toBeVisible();
  await expect(page.locator("#sign-out")).toBeHidden();
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#tenant-panel")).toBeHidden();
  await expect(page.locator("#key-new")).toHaveValue("");
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session"))).toBeNull();
});

test("revoked owner key-create 401 clears session before processing one-time secret", async ({page, request}) => {
  await owner(page, request, {keyMutationStatus: 401});
  await page.locator("#key-create").click();
  await expect(page.locator("#account-status")).toContainText("expired or been revoked");
  await expect(page.locator("#sign-in")).toBeVisible();
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#tenant-panel")).toBeHidden();
  await expect(page.locator("#key-new")).toHaveValue("");
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session"))).toBeNull();
});
