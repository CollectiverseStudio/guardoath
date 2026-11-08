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
