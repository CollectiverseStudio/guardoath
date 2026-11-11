const {test, expect} = require("@playwright/test");

test("ordinary tenant sees checks first and advanced configuration closed by default", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  const advanced = page.locator("#comparison-configuration");
  await expect(page.locator("#tenant-checks")).toBeVisible();
  await expect(page.locator("#check-settings-heading")).toHaveText("Your checks and rules");
  await expect(advanced).toBeVisible();
  await expect(advanced).not.toHaveAttribute("open", "");
  await expect(page.locator("#comparison-editor")).toBeHidden();
  await advanced.locator("summary").click();
  await expect(advanced).toHaveAttribute("open", "");
  await expect(page.locator("#comparison-editor")).toBeVisible();
  await expect(advanced).toContainText("Arithmetic comparisons");
  await expect(advanced).toContainText("Host-attested reference status");
  await advanced.locator("summary").click();
  await expect(page.locator("#comparison-editor")).toBeHidden();
  // Merely revealing or collapsing UI must not save or change tenant policy.
  await expect(page.locator("#tenant-message")).not.toContainText("Saved.");
});
