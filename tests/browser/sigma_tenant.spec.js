const {test, expect} = require("@playwright/test");

async function connectDemo(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
}

test("Sigma source rules appear as off-by-default settings on the tenant record", async ({page}) => {
  await connectDemo(page);
  await page.locator("#tenant-sigma-group > summary").click();
  await expect(page.locator("#sigma-rule-table tbody tr")).toHaveCount(228);
  await expect(page.locator("#sigma-rule-count")).toContainText("228 of 228");
  const first = page.locator("#sigma-rule-table tbody tr").first();
  await expect(first.locator("select")).toHaveValue("");
  await page.locator("#sigma-rule-search").fill("will-not-match-any-rule-XYZ");
  await expect(page.locator("#sigma-rule-count")).toContainText("0 of 228");
  await page.locator("#sigma-rule-search").fill("");
  await expect(page.locator("#sigma-rule-table tbody tr:visible")).toHaveCount(228);
});

test("Sigma action saves through tenant revision and is restored on reload", async ({page}) => {
  await connectDemo(page);
  await page.locator("#tenant-sigma-group > summary").click();
  await expect(page.locator("#sigma-rule-table tbody tr")).toHaveCount(228);
  const first = page.locator("#sigma-rule-table tbody tr").first();
  const id = await first.getAttribute("data-sigma-id");
  await first.locator("select").selectOption("flag");
  await expect(page.locator("#tenant-message")).toContainText("Unsaved changes");
  await page.locator("#tenant-save").click();
  await expect(page.locator("#tenant-message")).toContainText("Saved.");
  await page.locator("#tenant-sigma-group > summary").click();
  await expect(page.locator('#sigma-rule-table tr[data-sigma-id="' + id + '"] select')).toHaveValue("flag");
  await page.locator('#sigma-rule-table tr[data-sigma-id="' + id + '"] button').first().click();
  await expect(page.locator('#sigma-rule-table tr[data-sigma-id="' + id + '"] [role="status"]')).toContainText("Recommended flag");
  await page.locator('#sigma-rule-table tr[data-sigma-id="' + id + '"] select').selectOption("");
  await page.locator("#tenant-save").click();
  await expect(page.locator("#tenant-message")).toContainText("Saved.");
});
