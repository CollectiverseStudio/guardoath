const {test, expect} = require("@playwright/test");

// UI-only contract for an already verified first-time Cognito session.
// The server's cryptographic Cognito and account registration/entitlement
// checks are separately tested in Python; these browser stubs do not stand
// in for production Google OAuth or paid-provider verification.
test("new Google session remains available to register instead of being discarded", async ({page}) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "synthetic-ui-session-only");
  });
  await page.route("**/v1/sign-in", async route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email", registration_enabled: true,
      google_provider: "Google"
    })
  }));
  await page.route("**/v1/account/status", async route => {
    expect(route.request().headers()["x-guardoath-session"]).toBe("synthetic-ui-session-only");
    await route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        registered: false, registration_enabled: true,
        tenant_id: null, subscription_status: "not_registered",
        can_validate: false
      })
    });
  });
  await page.goto("/dashboard");
  await expect(page.locator("#sign-in-box")).toBeVisible();
  await expect(page.locator("#register-account")).toBeVisible();
  await expect(page.locator("#account-status")).toContainText("Registration");
  await expect(page.locator("#account-status")).toContainText("paid subscription");
  await expect(page.locator("#connection-status")).toContainText("registration pending");
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session")))
    .toBe("synthetic-ui-session-only");
});


test("registered but unpaid customer sees inactive plan and cannot click Create API key", async ({page, request}) => {
  // Start from a REAL local application summary; only external Google session
  // and unprovisioned paid-customer identity are synthetic. The backend 402
  // API-key refusal is covered separately by Python tenant HTTP regression.
  const issued = await request.post("/v1/demo-users/demo2/key", {data: {}});
  expect(issued.status()).toBe(200);
  const demoKey = (await issued.json()).api_key;
  const sample = await request.get("/v1/applications/current", {
    headers: {"X-GuardOath-Key": demoKey}
  });
  expect(sample.status()).toBe(200);
  const application = await sample.json();
  application.application_id = "usr-synthetic-unpaid";
  application.tenant = {
    ...application.tenant,
    tenant_id: "usr-synthetic-unpaid",
    display_name: "Unpaid account",
    demo: false, subscription_status: "inactive", subscription_active: false,
    monthly_allowance: 0, used: 0
  };
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "synthetic-ui-session-only");
  });
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email", registration_enabled: true
    })
  }));
  await page.route("**/v1/account/status", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      registered: true, registration_enabled: true,
      tenant_id: "usr-synthetic-unpaid", subscription_status: "inactive",
      can_validate: false
    })
  }));
  await page.route("**/v1/applications/current", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(application)
  }));
  await page.route("**/v1/tenants/current/keys", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({keys: [], max_keys: 2})
  }));
  await page.goto("/dashboard");
  await expect(page.locator("#account-status")).toContainText("Subscription: inactive");
  await expect(page.locator("#subscription-status")).toContainText("No active subscription");
  await expect(page.locator("#keys-panel")).toBeVisible();
  await expect(page.locator("#key-create")).toBeDisabled();
  await expect(page.locator("#key-message")).toContainText("active subscription");
  await expect(page.locator("#billing-subscribe")).toBeVisible();
  await expect(page.locator("#billing-manage")).toBeVisible();
  let checkoutCalls = 0;
  await page.route("**/v1/billing/checkout", async route => {
    checkoutCalls++;
    expect(route.request().headers()["x-guardoath-session"]).toBe("synthetic-ui-session-only");
    await route.fulfill({status: 409, contentType: "application/json",
      body: JSON.stringify({detail: "Stripe customer provisioning is required"})});
  });
  await page.locator("#billing-subscribe").click();
  await expect(page.locator("#account-status")).toContainText("Billing setup is not yet available");
  expect(checkoutCalls).toBe(1);
  let portalCalls = 0;
  await page.route("**/v1/billing/portal", async route => {
    portalCalls++;
    expect(route.request().headers()["x-guardoath-session"]).toBe("synthetic-ui-session-only");
    await route.fulfill({status: 409, contentType: "application/json",
      body: JSON.stringify({detail: "No Stripe customer binding"})});
  });
  await page.locator("#billing-manage").click();
  await expect(page.locator("#account-status")).toContainText("Billing setup is not yet available");
  expect(portalCalls).toBe(1);


});
