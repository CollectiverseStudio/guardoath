const {test, expect} = require("@playwright/test");

// Real demo tenant, real dashboard and saved record; do not mock tenant APIs.
test("policy setup wizard shows Input, Output and review without saving until Save checks", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo3");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo3");
  const token = await page.locator("#demo-key-value").inputValue();
  const current = async () => {
    const response = await request.get("/v1/applications/current",
      {headers: {Authorization: "Bearer " + token}});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  const initial = await current();
  const input = page.locator("#tenant-check-input-lexical_guards");
  const output = page.locator("#tenant-check-output-lexical_guards");
  const initialInput = await input.inputValue();
  const initialOutput = await output.inputValue();
  await expect(page.getByRole("button", {name: "Start setup wizard"})).toBeVisible();
  await page.locator("#checks-wizard-start").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 1 of 3 · Input protection");
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
  await expect(input).toBeVisible();
  await expect(output).toBeHidden();
  const changedInput = initialInput === "block" ? "flag" : "block";
  await input.selectOption(changedInput);
  await page.locator("#checks-wizard-next").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 2 of 3 · Output protection");
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "output");
  await expect(input).toBeHidden();
  await expect(output).toBeVisible();
  await expect(page.locator("#tenant-stage-hidden-draft")).toContainText("Unsaved Input");
  expect((await current()).revision).toBe(initial.revision);
  await page.locator("#checks-wizard-next").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 3 of 3 · Review and save");
  await expect(page.locator("#checks-wizard-next")).toBeHidden();
  await expect(input).toBeVisible();
  await expect(output).toBeVisible();
  await expect(input).toHaveValue(changedInput);
  expect((await current()).revision).toBe(initial.revision);
  // Exit does not save. Restore the original draft without changing the shared demo record.
  await page.locator("#checks-wizard-exit").click();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "both");
  await expect(page.locator("#checks-wizard-start")).toBeVisible();
  await input.selectOption(initialInput);
  await expect(output).toHaveValue(initialOutput);
  expect((await current()).revision).toBe(initial.revision);
});

test("the guided setup appears separately from conversation-testing steps", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#checks-setup-wizard")).toBeVisible();
  await expect(page.locator("#conversation-wizard")).toBeVisible();
  await expect(page.locator("#checks-wizard-guidance")).toContainText("Input protection");
  await page.locator("#checks-wizard-start").click();
  await expect(page.locator("#checks-wizard-guidance")).toContainText("BEFORE sending it to AI");
  await page.locator("#checks-wizard-next").click();
  await expect(page.locator("#checks-wizard-guidance")).toContainText("BEFORE returning it to the user");
  await page.locator("#checks-wizard-back").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 1 of 3");
});


test("switching demo tenants exits active setup and restores both columns", async ({page}) => {
  await page.goto("/dashboard");
  const connect = async tenant => {
    await page.locator("#demo-user").selectOption(tenant);
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to " + tenant);
  };
  await connect("demo3");
  await page.locator("#checks-wizard-start").click();
  await page.locator("#checks-wizard-next").click();
  await expect(page.locator("#checks-wizard-progress")).toContainText("Step 2 of 3");
  await connect("demo4");
  await expect(page.locator("#checks-wizard-progress")).toHaveText("Guided setup available");
  await expect(page.locator("#checks-wizard-start")).toBeVisible();
  await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "both");
  await expect(page.locator("#tenant-check-input-lexical_guards")).toBeVisible();
  await expect(page.locator("#tenant-check-output-lexical_guards")).toBeVisible();
});


test("wizard tracks manual stage tabs and review Save uses the existing revisioned tenant action", async ({page, request}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo3");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo3");
  const token = await page.locator("#demo-key-value").inputValue();
  const current = async () => {
    const response = await request.get("/v1/applications/current",
      {headers: {Authorization: "Bearer " + token}});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant.revision;
  };
  const initialRevision = await current();
  const input = page.locator("#tenant-check-input-lexical_guards");
  const initialInput = await input.inputValue();
  const nextInput = initialInput === "block" ? "flag" : "block";
  try {
    await page.locator("#checks-wizard-start").click();
    await input.selectOption(nextInput);
    await page.locator("#tenant-tab-output").click();
    await expect(page.locator("#checks-wizard-progress")).toContainText("Step 2 of 3 · Output protection");
    await expect(page.locator("#checks-wizard-save")).toBeHidden();
    await expect(page.locator("#tenant-stage-hidden-draft")).toContainText("Unsaved Input");
    await page.locator("#tenant-tab-input").click();
    await expect(page.locator("#checks-wizard-progress")).toContainText("Step 1 of 3 · Input protection");
    await expect(input).toHaveValue(nextInput);
    await page.locator("#tenant-tab-both").click();
    await expect(page.locator("#checks-wizard-progress")).toContainText("Step 3 of 3 · Review and save");
    await expect(page.locator("#checks-wizard-save")).toBeVisible();
    expect(await current()).toBe(initialRevision);
    await page.locator("#checks-wizard-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    expect(await current()).not.toBe(initialRevision);
    await page.reload();
    await page.locator("#demo-user").selectOption("demo3");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#tenant-check-input-lexical_guards")).toHaveValue(nextInput);
  } finally {
    await page.locator("#tenant-tab-both").click();
    await page.locator("#tenant-check-input-lexical_guards").selectOption(initialInput);
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
  }
});
