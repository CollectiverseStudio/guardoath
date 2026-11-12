const {test, expect} = require("@playwright/test");

test("demo API key and tenant checks survive an unavailable optional Sigma script", async ({page, request}) => {
  // Reproduce a missing/stale deployment asset; do not mock authentication or
  // any tenant/check APIs. The real authenticated demo connection must work.
  await page.route("**/assets/sigma_tenant.js**", route =>
    route.fulfill({status: 503, contentType: "text/plain", body: "unavailable"}));
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  await expect(page.locator("#sigma-editor-unavailable")).toContainText(
    "its saved settings are unchanged");
  await expect(page.locator("#tenant-checks #check-table")).toBeVisible();

  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const current = async () => {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  const before = await current();
  try {
    expect(await page.evaluate(() => typeof window.sigmaTenant)).toBe("undefined");
  // Saving an ordinary check must not synthesize an empty Sigma policy or
  // discard previously stored Sigma event actions.
  const chosen = before.input_checks.schema === "block" ? "flag" : "block";
  await page.locator("#tenant-check-input-schema").selectOption(chosen);
  await page.getByRole("button", {name: "Save checks", exact: true}).click();
  await expect(page.locator("#tenant-message")).toContainText("Saved.");
  const after = await current();
  expect(after.input_checks.schema).toBe(chosen);
  expect(after.sigma_rule_actions).toEqual(before.sigma_rule_actions);

  } finally {
    // Restore the shared demo tenant even when an assertion fails.
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
  }
});
