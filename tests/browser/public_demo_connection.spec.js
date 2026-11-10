const {test, expect} = require("@playwright/test");

// Demo validation is intentionally public, but an application key never
// authenticates a customer owner or opens their API-key management controls.
test("public demo key connects to its own tenant without customer sign-in", async ({page}) => {
  await page.goto("/dashboard");
  await expect(page.locator("#demo-users")).toBeVisible();
  await expect(page.locator("#customer-key-connect")).toBeHidden();
  await expect(page.locator("#api-key")).toBeHidden();
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  await expect(page.locator("#policy")).toHaveValue("tenant");
  await expect(page.locator("#run")).toBeEnabled();
  await expect(page.locator("#demo-key-value")).not.toHaveValue("");
  await expect(page.locator("#customer-key-connect")).toBeHidden();
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#sign-out")).toBeHidden();
  await expect(page.locator("#api-key")).toBeHidden();
});

test("public demo refuses a key resolving to a different tenant", async ({page, request}) => {
  const issued = await request.post("/v1/demo-users/demo4/key", {data: {}});
  expect(issued.status()).toBe(200);
  const current = await request.get("/v1/applications/current", {
    headers: {"X-GuardOath-Key": (await issued.json()).api_key}
  });
  expect(current.status()).toBe(200);
  const mismatched = await current.json();
  expect(mismatched.tenant.demo).toBe(true);
  mismatched.tenant.tenant_id = "demo2";
  await page.route("**/v1/applications/current", route => route.fulfill({
    status: 200, contentType: "application/json", body: JSON.stringify(mismatched)
  }));
  await page.goto("/dashboard");
  await expect(page.locator("#demo-users")).toBeVisible();
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("did not match the selected demo user");
  await expect(page.locator("#run")).toBeDisabled();
  await expect(page.locator("#tenant-panel")).toBeHidden();
  await expect(page.locator("#demo-key-value")).toHaveValue("");
  await expect(page.locator("#keys-panel")).toBeHidden();
  await expect(page.locator("#api-key")).toBeHidden();
});
