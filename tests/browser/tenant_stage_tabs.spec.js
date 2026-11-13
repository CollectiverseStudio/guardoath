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

test("connecting another tenant resets stage visibility without changing either tenant record", async ({ page, request }) => {
  await page.goto("/dashboard");
  const connect = async demo => {
    await page.locator("#demo-user").selectOption(demo);
    await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to " + demo);
    const token = await page.locator("#demo-key-value").inputValue();
    const headers = { Authorization: "Bearer " + token };
    const response = await request.get("/v1/applications/current", { headers });
    expect(response.status(), (await response.text()).slice(0, 120)).toBe(200);
    return {headers, revision: (await response.json()).tenant.revision};
  };
  const demo3 = await connect("demo3");
  await page.locator("#tenant-tab-input").click();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
  await expect(page.locator("#tenant-check-output-cross_field")).toBeHidden();

  const demo4 = await connect("demo4");
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "both");
  await expect(page.locator("#tenant-check-input-cross_field")).toBeVisible();
  await expect(page.locator("#tenant-check-output-cross_field")).toBeVisible();
  await expect(page.locator("#tenant-tab-both")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#tenant-stage-description")).toContainText("Showing Input and Output");

  await page.locator("#tenant-tab-output").click();
  await expect(page.locator("#tenant-check-input-cross_field")).toBeHidden();
  await connect("demo3");
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "both");
  await expect(page.locator("#tenant-check-input-cross_field")).toBeVisible();
  await expect(page.locator("#tenant-check-output-cross_field")).toBeVisible();

  for (const tenant of [demo3, demo4]) {
    const response = await request.get("/v1/applications/current", {headers: tenant.headers});
    expect(response.status(), (await response.text()).slice(0, 120)).toBe(200);
    expect((await response.json()).tenant.revision).toBe(tenant.revision);
  }
});
