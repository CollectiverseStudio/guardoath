const {test, expect} = require("@playwright/test");

const orderedModules = [
  "statistical_drift_editor.js", "arithmetic_editor.js", "identity_editor.js",
  "conversion_editor.js", "comparison_editor.js", "sigma_tenant.js", "dashboard.js",
  "conversation_wizard.js", "tenant_stage_tabs.js", "agent_activity.js"
];

async function savedDemo(request) {
  // The local memory-backed server only; no customer/provider API is mocked.
  const keyResponse = await request.post("/v1/demo-users/demo9/key", {data: {}});
  expect(keyResponse.status()).toBe(200);
  const headers = {Authorization: "Bearer " + (await keyResponse.json()).api_key};
  const read = async () => {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  return {before: await read(), read};
}

async function connectDemo(page) {
  await page.locator("#demo-user").selectOption("demo9");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo9");
  await expect(page.locator("#tenant-checks #check-table")).toBeVisible();
  await expect(page.locator("#run-input")).toBeEnabled();
  // Loading and navigation never authorize unchecked output.
  await expect(page.locator("#run-output")).toBeDisabled();
}

test("serial startup recovers a throttled editor and initializes controls after DOMContentLoaded", async ({page, request}) => {
  const saved = await savedDemo(request);
  const requested = [], lateStates = [], errors = [];
  let active = 0, maximum = 0, comparisonAttempts = 0;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/assets/*.js*", async route => {
    const name = new URL(route.request().url()).pathname.split("/").pop();
    requested.push(name);
    active++;
    maximum = Math.max(maximum, active);
    try {
      if (name === "statistical_drift_editor.js") await page.waitForLoadState("domcontentloaded");
      if (["conversation_wizard.js", "tenant_stage_tabs.js", "agent_activity.js"].includes(name)) {
        lateStates.push(await page.evaluate(() => document.readyState));
      }
      // A little response latency makes parallel native script loading observable.
      await new Promise(resolve => setTimeout(resolve, 20));
      if (name === "comparison_editor.js" && ++comparisonAttempts === 1) {
        active--;
        await route.fulfill({status: 429, contentType: "application/json",
          body: '{"message":"ConcurrentInvocationLimitExceeded"}'});
        return;
      }
      const response = await route.fetch();
      active--;
      await route.fulfill({response});
    } catch (error) {
      active = Math.max(0, active - 1);
      throw error;
    }
  });
  await page.goto("/dashboard");
  await expect(page.locator("#dashboard-startup")).toBeHidden();
  await expect(page.locator("#dashboard-app")).toBeVisible();
  expect(maximum).toBe(1);
  expect(comparisonAttempts).toBe(2);
  expect(requested).toEqual(["dashboard_bootstrap.js", ...orderedModules.slice(0, 5),
    "comparison_editor.js", ...orderedModules.slice(5)]);
  expect(lateStates).toHaveLength(3);
  expect(lateStates.every(state => state !== "loading")).toBe(true);
  await connectDemo(page);
  await page.locator("#tenant-tab-output").click();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "output");
  await page.locator("#wizard-start").click();
  await expect(page.locator("#wizard-status")).toHaveText("Step 1 of 4");
  await page.locator("#wizard-next").click();
  await expect(page.locator("#wizard-status")).toHaveText("Step 2 of 4");
  await page.locator("#agent-sample-missing").click();
  await expect(page.locator("#agent-proposed")).toHaveValue(/"tool": "shell\.run"/);
  await expect(page.locator("#agent-observed")).toBeEmpty();
  await expect(page.locator("#agent-activity-result")).toContainText("Synthetic example loaded");
  expect(await saved.read()).toEqual(saved.before);
  expect(errors).toEqual([]);
});

test("persistent asset failure stays locked and manual retry resumes once without API startup or policy changes", async ({page, request}) => {
  const saved = await savedDemo(request);
  const counts = new Map(), apiRequests = [];
  let available = false;
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/v1/")) apiRequests.push(path);
  });
  await page.route("**/assets/*.js*", async route => {
    const name = new URL(route.request().url()).pathname.split("/").pop();
    counts.set(name, (counts.get(name) || 0) + 1);
    if (name === "agent_activity.js" && !available) {
      await route.fulfill({status: 503, contentType: "text/plain", body: "unavailable"});
    } else await route.continue();
  });
  await page.goto("/dashboard");
  const retry = page.getByRole("button", {name: "Retry loading dashboard", exact: true});
  await expect(retry).toBeVisible();
  await expect(page.locator("#dashboard-startup-message")).toContainText("Your settings have not changed");
  await expect(page.locator("#dashboard-app")).toBeHidden();
  await expect(page.locator("#dashboard-app")).toHaveJSProperty("inert", true);
  await expect(page.locator("#run-input")).toBeDisabled();
  expect(counts.get("agent_activity.js")).toBe(3);
  expect(apiRequests).toEqual([]);
  expect(await saved.read()).toEqual(saved.before);

  available = true;
  await retry.click();
  await expect(page.locator("#dashboard-startup")).toBeHidden();
  await expect(page.locator("#dashboard-app")).toHaveJSProperty("inert", false);
  await connectDemo(page);
  expect(counts.get("agent_activity.js")).toBe(4);
  for (const name of ["dashboard_bootstrap.js", ...orderedModules.slice(0, -1)]) {
    expect(counts.get(name), name).toBe(1);
  }
  expect(apiRequests.filter(path => path === "/v1/demo-users")).toHaveLength(1);
  expect(apiRequests.filter(path => path === "/v1/sign-in")).toHaveLength(1);
  await page.locator("#agent-sample-matching").click();
  await expect(page.locator("#agent-activity-result")).toContainText("Synthetic example loaded");
  expect(await saved.read()).toEqual(saved.before);
});

for (const missing of ["comparison_editor.js", "conversation_wizard.js"]) {
test(`a required ${missing} that loads without initializing never exposes a partial dashboard`, async ({page}) => {
  const apiRequests = [];
  let attempts = 0;
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/v1/")) apiRequests.push(path);
  });
  await page.route("**/assets/" + missing + "**", async route => {
    attempts++;
    await route.fulfill({status: 200, contentType: "text/javascript", body: '"use strict";'});
  });
  await page.goto("/dashboard");
  await expect(page.getByRole("button", {name: "Reload dashboard", exact: true})).toBeVisible();
  await expect(page.locator("#dashboard-app")).toBeHidden();
  await expect(page.locator("#dashboard-app")).toHaveJSProperty("inert", true);
  expect(attempts).toBe(1);
  expect(apiRequests).toEqual([]);
});
}

test("the native reload link recovers when the bootstrap itself was unavailable", async ({page}) => {
  let available = false, bootstrapRequests = 0, dashboardRequests = 0;
  const apiRequests = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path === "/dashboard") dashboardRequests++;
    if (path.startsWith("/v1/")) apiRequests.push(path);
  });
  await page.route("**/assets/dashboard_bootstrap.js**", async route => {
    bootstrapRequests++;
    if (available) await route.continue();
    else await route.fulfill({status: 503, contentType: "text/plain", body: "unavailable"});
  });
  const response = await page.goto("/dashboard");
  expect(response.headers()["cache-control"]).toBe("no-store");
  await expect(page.locator("#dashboard-app")).toBeHidden();
  const reload = page.getByRole("link", {name: "reload this page", exact: true});
  await expect(reload).toBeVisible();
  await expect(reload).toHaveAttribute("href", "/dashboard");
  expect(apiRequests).toEqual([]);
  available = true;
  await reload.click();
  await expect(page.locator("#dashboard-startup")).toBeHidden();
  await expect(page.locator("#dashboard-app")).toBeVisible();
  await expect(page.locator("#demo-users")).toBeVisible();
  expect(dashboardRequests).toBe(2);
  expect(bootstrapRequests).toBe(2);
});
