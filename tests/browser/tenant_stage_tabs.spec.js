const { test, expect } = require("@playwright/test");

// Exercise the real dashboard with a real demo tenant, not synthetic state.
test("Input and Output tabs preserve both saved actions across reconnect", async ({ page }) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo3");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo3");

  const input = page.locator("#tenant-check-input-lexical_guards");
  const output = page.locator("#tenant-check-output-lexical_guards");
  const originalInput = await input.inputValue();
  const originalOutput = await output.inputValue();
  try {
    await page.locator("#tenant-tab-input").click();
    await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
    await expect(input).toBeVisible();
    await expect(output).toBeHidden();
    await input.selectOption("block");

    await page.locator("#tenant-tab-output").click();
    await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "output");
    await expect(input).toBeHidden();
    await expect(output).toBeVisible();
    await output.selectOption("flag");

    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    await page.reload();
    await page.locator("#demo-user").selectOption("demo3");
    await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo3");
    await expect(page.locator("#tenant-check-input-lexical_guards")).toHaveValue("block");
    await expect(page.locator("#tenant-check-output-lexical_guards")).toHaveValue("flag");
    await page.locator("#tenant-tab-both").click();
    await expect(page.locator("#tenant-check-input-lexical_guards")).toBeVisible();
    await expect(page.locator("#tenant-check-output-lexical_guards")).toBeVisible();
  } finally {
    await page.locator("#tenant-tab-both").click();
    await page.locator("#tenant-check-input-lexical_guards").selectOption(originalInput);
    await page.locator("#tenant-check-output-lexical_guards").selectOption(originalOutput);
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
  }
});

test("reviewing a finding focuses its stage without changing policy", async ({ page }) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  await page.locator("#tenant-tab-output").click();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "output");
  const original = await page.locator("#tenant-check-input-lexical_guards").inputValue();
  await page.evaluate(() => reviewFindingRule("lexical_guards.system_prompt", "lexical_guards", "input"));
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
  await expect(page.locator("#tenant-check-input-lexical_guards")).toHaveValue(original);
});
