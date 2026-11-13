const { test, expect } = require("@playwright/test");

test("guided conversation preserves input content and never unlocks output before input passes", async ({ page }) => {
  await page.goto("/dashboard");
  const input = page.locator("#input-content");
  const original = "Please summarize the customer return policy.";
  await input.fill(original);
  await page.locator("#wizard-start").click();
  await expect(page.locator("#wizard-input")).toBeVisible();
  await expect(page.locator("#wizard-output")).toBeHidden();
  await expect(page.locator("#wizard-status")).toHaveText("Step 1 of 4");

  await page.locator("#wizard-next").click();
  await expect(page.locator("#wizard-input-check")).toBeVisible();
  await expect(page.locator("#wizard-input")).toBeHidden();
  await page.locator("#wizard-next").click();
  await expect(page.locator("#wizard-output")).toBeVisible();
  await expect(page.locator("#output-content")).toHaveAttribute("readonly", "");
  await expect(page.locator("#run-output")).toBeDisabled();

  await page.locator("#wizard-back").click();
  await page.locator("#wizard-back").click();
  await expect(input).toHaveValue(original);
  await page.locator("#wizard-show-all").click();
  for (const id of ["wizard-input", "wizard-input-check", "wizard-output", "wizard-output-check"]) {
    await expect(page.locator("#" + id)).toBeVisible();
  }
  await expect(input).toHaveValue(original);
});

test("selected example context explains all enabled checks can report separate findings", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.locator("#wizard-finding-context")).toContainText("All enabled Input checks run");
  await expect(page.locator("#wizard-finding-context")).toContainText("unrelated integrity or safety findings are separate");
  await page.locator("#wizard-start").click();
  await page.locator("#wizard-next").click();
  await expect(page.locator("#wizard-finding-context")).toBeVisible();
});
