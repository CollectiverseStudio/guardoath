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
  await expect(page.locator("#billing-manage")).toBeHidden();
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



});


test("deployment without registration preserves native Cognito login", async ({page}) => {
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email", registration_enabled: false, google_provider: null
    })
  }));
  await page.goto("/dashboard");
  await expect(page.locator("#sign-in-box")).toBeVisible();
  await expect(page.locator("#sign-in")).toBeVisible();
  await expect(page.locator("#sign-in")).toHaveText("Sign in to GuardOath");
  await expect(page.locator("#register-account")).toBeHidden();
  await expect(page.locator("#account-status")).toContainText("Public registration is not enabled");
  await page.route("https://example.invalid/oauth2/authorize**", route => route.fulfill({
    status: 200, contentType: "text/html", body: "<html><body>Native Cognito login stub</body></html>"
  }));
  const authRequest = page.waitForRequest(r => r.url().startsWith("https://example.invalid/oauth2/authorize"));
  await page.locator("#sign-in").click();
  const redirect = new URL((await authRequest).url());
  expect(redirect.searchParams.get("identity_provider")).toBeNull();
  expect(redirect.searchParams.get("response_type")).toBe("code");
  expect(redirect.searchParams.get("code_challenge_method")).toBe("S256");
  expect(redirect.searchParams.get("code_challenge")).toBeTruthy();
  expect(redirect.searchParams.get("state")).toBeTruthy();
});


test("verified Google session survives transient registration failure and retries as unpaid", async ({page, request}) => {
  const issued = await request.post("/v1/demo-users/demo2/key", {data: {}});
  expect(issued.status()).toBe(200);
  const demoKey = (await issued.json()).api_key;
  const original = await request.get("/v1/applications/current", {
    headers: {"X-GuardOath-Key": demoKey}
  });
  expect(original.status()).toBe(200);
  const application = await original.json();
  application.application_id = "usr-retry-unpaid";
  application.tenant = {
    ...application.tenant, tenant_id: "usr-retry-unpaid", display_name: "New customer",
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
      logout_url: "https://example.invalid/logout", client_id: "synthetic",
      redirect_uri: "http://localhost/dashboard", scope: "openid email",
      registration_enabled: true, google_provider: "Google"
    })
  }));
  let registered = false, calls = 0;
  await page.route("**/v1/account/status", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      registered, registration_enabled: true,
      tenant_id: registered ? "usr-retry-unpaid" : null,
      subscription_status: registered ? "inactive" : "not_registered",
      can_validate: false
    })
  }));
  await page.route("**/v1/register", async route => {
    calls++;
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["x-guardoath-session"]).toBe("synthetic-ui-session-only");
    expect(route.request().headers()["content-type"]).toContain("application/json");
    expect(route.request().postData()).toBe("{}");
    if (calls === 1) {
      await route.fulfill({status: 503, contentType: "application/json",
        body: JSON.stringify({detail: "Registration unavailable"})});
    } else {
      registered = true;
      await route.fulfill({status: 201, contentType: "application/json",
        body: JSON.stringify({
          tenant_id: "usr-retry-unpaid", subscription_status: "inactive",
          can_validate: false, next_step: "Choose a subscription"
        })});
    }
  });
  await page.route("**/v1/applications/current", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(application)
  }));
  await page.route("**/v1/tenants/current/keys", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify({keys: [], max_keys: 2})
  }));

  await page.goto("/dashboard");
  await expect(page.locator("#register-account")).toBeVisible();
  await page.locator("#register-account").click();
  await expect(page.locator("#account-status")).toContainText("Registration not completed (HTTP 503)");
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session")))
    .toBe("synthetic-ui-session-only");
  await expect(page.locator("#register-account")).toBeEnabled();
  await page.locator("#register-account").click();
  await expect(page.locator("#account-status")).toContainText("Subscription: inactive");
  await expect(page.locator("#register-account")).toBeHidden();
  await expect(page.locator("#key-create")).toBeDisabled();
  expect(calls).toBe(2);
});


test("a disabled signed-in identity cannot register or see tenant controls", async ({page}) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "synthetic-disabled-session");
  });
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email", registration_enabled: true, google_provider: "Google"
    })
  }));
  await page.route("**/v1/account/status", route => route.fulfill({
    status: 403, contentType: "application/json",
    body: JSON.stringify({detail: "Account is unavailable; contact support"})
  }));
  await page.goto("/dashboard");
  await expect(page.locator("#account-status")).toContainText("unavailable");
  await expect(page.locator("#account-status")).toContainText("Contact support");
  await expect(page.locator("#register-account")).toBeHidden();
  await expect(page.locator("#sign-out")).toBeVisible();
  await expect(page.locator("#keys-panel")).toBeHidden();
});


test("Google login starts Cognito authorization code with PKCE and explicit Google provider", async ({page}) => {
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email aws.cognito.signin.user.admin",
      registration_enabled: true, google_provider: "Google"
    })
  }));
  await page.goto("/dashboard");
  await expect(page.locator("#sign-in")).toBeVisible();
  await expect(page.locator("#sign-in")).toHaveText("Sign in with Google");
  await expect(page.locator("#register-account")).toBeVisible();
  await page.route("https://example.invalid/oauth2/authorize**", route => route.fulfill({
    status: 200, contentType: "text/html", body: "<html><body>OAuth stub</body></html>"
  }));
  const authRequest = page.waitForRequest(r =>
    r.url().startsWith("https://example.invalid/oauth2/authorize"));
  await page.locator("#sign-in").click();
  const auth = new URL((await authRequest).url());
  expect(auth.searchParams.get("identity_provider")).toBe("Google");
  expect(auth.searchParams.get("response_type")).toBe("code");
  expect(auth.searchParams.get("client_id")).toBe("synthetic");
  expect(auth.searchParams.get("redirect_uri")).toBe("http://localhost/dashboard");
  expect(auth.searchParams.get("scope")).toContain("aws.cognito.signin.user.admin");
  expect(auth.searchParams.get("code_challenge_method")).toBe("S256");
  expect(auth.searchParams.get("code_challenge")).toMatch(/^[A-Za-z0-9_-]{43}$/);
  expect(auth.searchParams.get("state")).toMatch(/^[A-Za-z0-9_-]{22}$/);
});


test("Google OAuth callback preserves verified session after transient automatic registration failure", async ({page, request}) => {
  const demo = await request.post("/v1/demo-users/demo2/key", {data: {}});
  expect(demo.status()).toBe(200);
  const summary = await request.get("/v1/applications/current", {
    headers: {"X-GuardOath-Key": (await demo.json()).api_key}
  });
  expect(summary.status()).toBe(200);
  const application = await summary.json();
  application.application_id = "usr-oauth-callback-unpaid";
  application.tenant = {
    ...application.tenant,
    tenant_id: "usr-oauth-callback-unpaid", display_name: "Registered Google account",
    demo: false, subscription_status: "inactive", subscription_active: false,
    monthly_allowance: 0, used: 0
  };
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-state", "synthetic-oauth-state");
    sessionStorage.setItem("guardoath-verifier", "v".repeat(43));
    sessionStorage.setItem("guardoath-sign-in-intent", "register");
  });
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email aws.cognito.signin.user.admin",
      registration_enabled: true, google_provider: "Google"
    })
  }));
  let tokenCalls = 0;
  await page.route("**/v1/sign-in/token", async route => {
    tokenCalls++;
    expect(route.request().headers()["content-type"]).toContain("application/json");
    expect(route.request().headers()["x-amz-content-sha256"]).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.parse(route.request().postData())).toEqual({
      code: "test-code", code_verifier: "v".repeat(43)
    });
    await route.fulfill({status: 200, contentType: "application/json",
      body: JSON.stringify({access_token: "synthetic-oauth-session", expires_in: 3600})});
  });
  let registered = false;
  let attempts = 0;
  await page.route("**/v1/register", async route => {
    attempts++;
    expect(route.request().headers()["x-guardoath-session"]).toBe("synthetic-oauth-session");
    expect(route.request().postData()).toBe("{}");
    expect(route.request().headers()["x-amz-content-sha256"]).toMatch(/^[a-f0-9]{64}$/);
    if (attempts === 1) {
      await route.fulfill({status: 503, contentType: "application/json",
        body: JSON.stringify({detail: "Temporary registration failure"})});
    } else {
      registered = true;
      await route.fulfill({status: 201, contentType: "application/json",
        body: JSON.stringify({tenant_id: "usr-oauth-callback-unpaid",
          subscription_status: "inactive", can_validate: false})});
    }
  });
  await page.route("**/v1/account/status", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      registered, registration_enabled: true,
      tenant_id: registered ? "usr-oauth-callback-unpaid" : null,
      subscription_status: registered ? "inactive" : "not_registered",
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

  await page.goto("/dashboard?code=test-code&state=synthetic-oauth-state");
  await expect(page.locator("#account-status")).toContainText("Registration not completed (HTTP 503)");
  expect(page.url()).not.toContain("code=test-code");
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session")))
    .toBe("synthetic-oauth-session");
  await expect(page.locator("#register-account")).toBeVisible();
  await page.locator("#register-account").click();
  await expect(page.locator("#account-status")).toContainText("Subscription: inactive");
  await expect(page.locator("#register-account")).toBeHidden();
  await expect(page.locator("#key-create")).toBeDisabled();
  expect(tokenCalls).toBe(1);
  expect(attempts).toBe(2);
});


test("expired Cognito session clears stored identity and restores customer sign-in", async ({page}) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "expired-synthetic-session");
  });
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email aws.cognito.signin.user.admin",
      registration_enabled: true, google_provider: "Google"
    })
  }));
  let checks = 0;
  await page.route("**/v1/account/status", async route => {
    checks++;
    expect(route.request().headers()["x-guardoath-session"])
      .toBe("expired-synthetic-session");
    await route.fulfill({status: 401, contentType: "application/json",
      body: JSON.stringify({detail: "Invalid or expired sign-in"})});
  });
  await page.goto("/dashboard");
  await expect(page.locator("#account-status")).toContainText("expired or been revoked");
  await expect(page.locator("#sign-in")).toBeVisible();
  await expect(page.locator("#register-account")).toBeHidden();
  await expect(page.locator("#sign-out")).toBeHidden();
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#tenant-panel")).toBeHidden();
  await expect(page.locator("#billing-subscribe")).toBeHidden();
  await expect(page.locator("#billing-manage")).toBeHidden();
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session"))).toBeNull();
  expect(checks).toBe(1);
});


test("revoked customer session during tenant settings load clears state and permits new login", async ({page}) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "revoked-after-status");
  });
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email aws.cognito.signin.user.admin",
      registration_enabled: true, google_provider: "Google"
    })
  }));
  await page.route("**/v1/account/status", route => {
    expect(route.request().headers()["x-guardoath-session"]).toBe("revoked-after-status");
    return route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({
        registered: true, registration_enabled: true, tenant_id: "usr-revoked",
        subscription_status: "active", can_validate: true
      })
    });
  });
  let requested = 0;
  await page.route("**/v1/applications/current", route => {
    requested++;
    expect(route.request().headers()["x-guardoath-session"]).toBe("revoked-after-status");
    return route.fulfill({
      status: 401, contentType: "application/json",
      body: JSON.stringify({detail: "Session was revoked"})
    });
  });
  await page.goto("/dashboard");
  await expect(page.locator("#account-status")).toContainText("expired or been revoked");
  await expect(page.locator("#sign-in")).toBeVisible();
  await expect(page.locator("#register-account")).toBeHidden();
  await expect(page.locator("#sign-out")).toBeHidden();
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#tenant-panel")).toBeHidden();
  await expect(page.locator("#billing-subscribe")).toBeHidden();
  await expect(page.locator("#billing-manage")).toBeHidden();
  expect(await page.evaluate(() => sessionStorage.getItem("guardoath-session"))).toBeNull();
  expect(requested).toBe(1);
});

test("native email registration is offered with Google disabled", async ({page}) => {
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout",
      client_id: "synthetic", redirect_uri: "http://localhost/dashboard",
      scope: "openid email", registration_enabled: true, google_provider: null
    })
  }));
  await page.goto("/dashboard");
  await expect(page.locator("#sign-in")).toHaveText("Sign in to GuardOath");
  await expect(page.locator("#register-account")).toBeVisible();
  await page.route("https://example.invalid/signup**", route => route.fulfill({
    status: 200, contentType: "text/html", body: "<html>Native Cognito sign-up stub</html>"
  }));
  const pending = page.waitForRequest(r => r.url().startsWith("https://example.invalid/signup"));
  await page.locator("#register-account").click();
  const target = new URL((await pending).url());
  expect(target.pathname).toBe("/signup");
  expect(target.searchParams.get("identity_provider")).toBeNull();
  expect(target.searchParams.get("code_challenge_method")).toBe("S256");
});

test("Stripe success redirect does not activate an unpaid account", async ({page, request}) => {
  const issued = await request.post("/v1/demo-users/demo2/key", {data: {}});
  expect(issued.status()).toBe(200);
  const demoKey = (await issued.json()).api_key;
  const summary = await request.get("/v1/applications/current", {
    headers: {"X-GuardOath-Key": demoKey}
  });
  const application = await summary.json();
  application.application_id = "usr-checkout-pending";
  application.tenant = {
    ...application.tenant, tenant_id: "usr-checkout-pending", demo: false,
    subscription_status: "inactive", subscription_active: false,
    monthly_allowance: 0
  };
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "synthetic-checkout-session");
  });
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout", client_id: "synthetic",
      redirect_uri: "http://localhost/dashboard", scope: "openid email",
      registration_enabled: true, google_provider: null})
  }));
  await page.route("**/v1/account/status", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({registered: true, registration_enabled: true,
      tenant_id: "usr-checkout-pending", subscription_status: "inactive",
      can_validate: false})
  }));
  await page.route("**/v1/applications/current", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(application)
  }));
  await page.route("**/v1/tenants/current/keys", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({keys: [], max_keys: 2})
  }));
  await page.goto("/dashboard?checkout=success");
  await expect(page.locator("#account-status")).toContainText("pending verification");
  await expect(page.locator("#key-create")).toBeDisabled();
  await expect(page.locator("#billing-manage")).toBeHidden();
  await expect(page.locator("#billing-subscribe")).toBeVisible();
});

test("public demo does not expose customer API-key management", async ({page}) => {
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({
      authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout", client_id: "synthetic",
      redirect_uri: "http://localhost/dashboard", scope: "openid email",
      registration_enabled: true, google_provider: null
    })
  }));
  await page.goto("/dashboard");
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#customer-key-connect")).toBeHidden();
  await expect(page.locator("#api-key")).toBeHidden();
  await expect(page.locator("#key-create")).toBeHidden();
  await expect(page.locator("#sign-in-box")).toBeVisible();
});

test("customer API-key management sends signed-in session, never pasted API key", async ({page, request}) => {
  const issued = await request.post("/v1/demo-users/demo2/key", {data: {}});
  expect(issued.status()).toBe(200);
  const demoKey = (await issued.json()).api_key;
  const summary = await request.get("/v1/applications/current", {
    headers: {"X-GuardOath-Key": demoKey}
  });
  const application = await summary.json();
  application.application_id = "usr-authenticated-keys";
  application.tenant = {...application.tenant, tenant_id: "usr-authenticated-keys",
    demo: false, subscription_status: "active", subscription_active: true};
  await page.addInitScript(() => {
    sessionStorage.setItem("guardoath-session", "verified-session-for-ui");
  });
  await page.route("**/v1/sign-in", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({authorize_url: "https://example.invalid/oauth2/authorize",
      logout_url: "https://example.invalid/logout", client_id: "synthetic",
      redirect_uri: "http://localhost/dashboard", scope: "openid email",
      registration_enabled: true, google_provider: null})
  }));
  await page.route("**/v1/account/status", route => route.fulfill({
    status: 200, contentType: "application/json",
    body: JSON.stringify({registered: true, registration_enabled: true,
      tenant_id: "usr-authenticated-keys", subscription_status: "active",
      can_validate: true})
  }));
  await page.route("**/v1/applications/current", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(application)
  }));
  let reads = 0, creates = 0;
  await page.route("**/v1/tenants/current/keys", async route => {
    const headers = route.request().headers();
    expect(headers["x-guardoath-session"]).toBe("verified-session-for-ui");
    expect(headers["x-guardoath-key"]).toBeUndefined();
    if (route.request().method() === "POST") {
      creates++;
      await route.fulfill({status: 200, contentType: "application/json",
        body: JSON.stringify({api_key: "gok_example_once"})});
    } else {
      reads++;
      await route.fulfill({status: 200, contentType: "application/json",
        body: JSON.stringify({keys: [], max_keys: 2})});
    }
  });
  await page.goto("/dashboard");
  await expect(page.locator("#keys-panel")).toBeVisible();
  await expect(page.locator("#customer-key-connect")).toBeVisible();
  await expect(page.locator("#key-create")).toBeEnabled();
  // The existing-key connection is offered only inside the authenticated view.
  await expect(page.locator("#api-key")).toBeVisible();
  await page.locator("#key-create").click();
  await expect(page.locator("#key-new")).toHaveValue("gok_example_once");
  expect(creates).toBe(1);
  expect(reads).toBeGreaterThan(0);
  // The one-time plaintext credential must not survive sign-out.
  await page.locator("#sign-out").click();
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#key-new")).toHaveValue("");
});
