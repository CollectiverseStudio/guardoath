const {test, expect} = require("@playwright/test");

// Actual Chrome browser and server UI: never mock the validation API.
test("guided steps retain Input data without unlocking unchecked Output", async ({page}) => {
  await page.goto("/dashboard");
  const steps = ["wizard-input", "wizard-input-check", "wizard-output", "wizard-output-check"];
  await expect(page.locator("#wizard-status")).toContainText("All four steps visible");
  await page.locator("#wizard-start").click();
  await expect(page.locator("#wizard-status")).toHaveText("Step 1 of 4");
  for (let i = 0; i < steps.length; i++) {
    if (i === 0) await expect(page.locator("#" + steps[i])).toBeVisible();
    else await expect(page.locator("#" + steps[i])).toBeHidden();
  }
  await page.locator("#input-content").fill("What is the return policy?");
  await page.locator("#wizard-next").click();
  await expect(page.locator("#wizard-input-check")).toBeVisible();
  await expect(page.locator("#wizard-input")).toBeHidden();
  await expect(page.locator("#run-input")).toBeDisabled();
  await page.locator('[data-wizard-step="2"]').click();
  await expect(page.locator("#wizard-output")).toBeVisible();
  await expect(page.locator("#output-content")).toHaveJSProperty("readOnly", true);
  await expect(page.locator("#input-gate")).toContainText("Complete the input test first");
  await page.locator('[data-wizard-step="3"]').click();
  await expect(page.locator("#run-output")).toBeDisabled();
  await expect(page.locator("#wizard-next")).toBeDisabled();
  await page.locator('[data-wizard-step="0"]').click();
  await expect(page.locator("#input-content")).toHaveValue("What is the return policy?");
  await expect(page.locator("#wizard-back")).toBeDisabled();
  await expect(page.locator("#wizard-finding-context")).toContainText("All enabled Input checks run");
});

test("prepared-example shortcut expands all steps yet preserves the Input gate", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#wizard-start").click();
  await expect(page.locator("#wizard-input-check")).toBeHidden();
  await page.locator("#batch-run > summary").click();
  await expect(page.locator("#batch-run")).toHaveAttribute("open", "");
  await expect(page.locator("#wizard-status")).toContainText("all four steps visible");
  for (const id of ["wizard-input", "wizard-input-check", "wizard-output", "wizard-output-check"]) {
    await expect(page.locator("#" + id)).toBeVisible();
  }
  await expect(page.locator("#run")).toBeDisabled();
  await expect(page.locator("#run-output")).toBeDisabled();
  await expect(page.locator("#output-content")).toHaveJSProperty("readOnly", true);
  await page.locator('[data-wizard-step="1"]').click();
  await expect(page.locator("#batch-run")).not.toHaveAttribute("open", "");
  await expect(page.locator("#wizard-status")).toHaveText("Step 2 of 4");
  await expect(page.locator("#wizard-output")).toBeHidden();
  await expect(page.locator("#wizard-input-check")).toBeVisible();
});
