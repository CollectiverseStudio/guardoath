const {test, expect} = require("@playwright/test");

// Customer-operable Field Comparisons example warnings, including the
// crucial distinction between a per-rule witness and a whole-tenant pass.
// Test the real authenticated UI, not a mocked rendering.
test("all Field Comparisons editors explain that other enabled checks may also find issues", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  for (const family of ["ordinary", "arithmetic", "identity", "conversion"]) {
    const note = page.locator('[data-field-example-scope="' + family + '"]');
    await expect(note).toHaveCount(1);
    await expect(note).toContainText("enabled");
  }
  await expect(page.locator("#tenant-message")).not.toContainText("Unsaved changes");
});
