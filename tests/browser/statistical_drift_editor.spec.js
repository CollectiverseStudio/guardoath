const {test, expect} = require("@playwright/test");

test("owner creates, saves, reloads, edits and executes a PSI batch rule in the dashboard", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo9");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo9");
  await page.getByRole("button", {name:"Reset to defaults", exact:true}).click();
  await expect(page.locator("#drift-editor-status")).toContainText("Owner-only reference configuration loaded");
  const auth = {Authorization:"Bearer " + await page.locator("#demo-key-value").inputValue()};
  const read = () => page.request.get("/v1/tenants/current/statistical-drift", {headers:auth});
  const row = page.locator('#drift-drafts li[data-drift-id="ui_psi_demo"]');
  try {
    await page.locator("#drift-synthetic-preset").click();
    await expect(page.locator("#drift-message")).toContainText("Synthetic demonstration only");
    await page.locator("#drift-apply").click();
    await expect(row).toHaveCount(1);
    expect((await (await read()).json()).rules).toEqual([]);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("flag");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    await expect(row).toHaveCount(1);
    let rules = (await (await read()).json()).rules;
    expect(rules).toHaveLength(1);
    expect(rules[0]).toMatchObject({rule_id:"ui_psi_demo", psi_threshold:"0.15",
      observations_pointer:"/income_samples", counts:[10,10,10], stages:["input","output"]});
    // The universal rule panel shows this tenant's dynamic rule, not a second engine.
    await page.locator("#rule-search").fill("ui_psi_demo");
    await expect(page.locator('tr.rule-row[data-rule="business.ui_psi_demo"]')).toBeVisible();
    await page.locator("#rule-search").fill("");

    await page.getByRole("button", {name:"Edit PSI ui_psi_demo", exact:true}).click();
    await page.locator("#drift-threshold").fill("0.2");
    await page.locator("#drift-apply").click();
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    rules = (await (await read()).json()).rules;
    expect(rules[0].psi_threshold).toBe("0.2");
    // A real persisted rule evaluates user-selected JSON via the real tenant API.
    await page.locator("#drift-synthetic-input").click();
    await expect(page.locator("#drift-message")).toContainText("shifted JSON batch loaded");
    const response = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
    await page.locator("#run-input").click();
    const outcome = await (await response).json();
    expect(outcome.findings.map(f => f.rule_id)).toContain("business.ui_psi_demo");
    expect(outcome.effective_action).toBe("block");
    await expect(page.locator("#answer")).toBeHidden();
  } finally {
    await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
    await page.getByRole("button", {name:"Reset to defaults", exact:true}).click();
    await expect(page.locator("#drift-editor-status")).toContainText("Owner-only reference configuration loaded");
    expect((await (await read()).json()).rules).toEqual([]);
  }
});

test("dashboard PSI editor rejects malformed drafts before tenant mutation", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo10");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo10");
  await page.getByRole("button", {name:"Reset to defaults", exact:true}).click();
  await expect(page.locator("#drift-editor-status")).toContainText("Owner-only reference configuration loaded");
  try {
    await page.locator("#drift-synthetic-preset").click();
    await page.locator("#drift-edges").fill("0,20,10,30");
    await page.locator("#drift-apply").click();
    await expect(page.locator("#drift-message")).toContainText("strictly increasing");
    await expect(page.locator("#drift-drafts li[data-drift-id]")).toHaveCount(0);
    await page.locator("#drift-synthetic-preset").click();
    await page.locator("#drift-counts").fill("10, true, 19");
    await page.locator("#drift-apply").click();
    await expect(page.locator("#drift-message")).toContainText("nonnegative integer");
    await expect(page.locator("#drift-drafts li[data-drift-id]")).toHaveCount(0);
  } finally {
    await page.getByRole("button", {name:"Reset to defaults", exact:true}).click();
  }
});
