const {test, expect} = require("@playwright/test");

// Exercise the real local FastAPI engine; no mocked check responses.
// An integrity mismatch is a detection, not automatically a Block.
test("allowed SHA-256 mismatch is explained without a red blocked label", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(node => { node.open = true; });
  await page.evaluate(() => {
    sessionStorage.setItem("guardoath-session", "synthetic-local-policy-fixture");
    application = {tenant: {demo: false}, policies: []};
    document.getElementById("customer-key-connect").hidden = false;
  });
  await page.getByLabel("Application API key").fill("playwright-test-key");
  await page.getByRole("button", {name: "Connect", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo");

  await page.locator("#policy").selectOption("integrity-v1");
  await page.locator("#input-example-check").selectOption("integrity");
  await page.locator("#input-example").selectOption("sample:integrity-1");
  await page.locator("#run-input").click();
  await expect(page.locator("#input-results .checker").filter({hasText: "Content integrity (SHA-256)"})).toBeVisible();
  const check = page.locator("#input-results .checker").filter({hasText: "Content integrity (SHA-256)"});
  await expect(check.locator(".pill")).toHaveText("Mismatch detected · Allowed");
  await expect(check.locator(".pill")).not.toHaveClass(/bad/);
  await expect(check).toContainText("structure.integrity_digest_mismatch");
  await expect(check.locator(".integrity-mismatch-context")).toContainText("Detection only — effective decision: Allow");
  await expect(page.locator("#input-results")).toContainText("Effective: allow");
});
