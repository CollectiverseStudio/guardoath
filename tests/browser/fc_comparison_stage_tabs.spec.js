// Actual customer browser -> revisioned tenant -> shared Input/Output Engine.
// Hiding one configuration column may never drop that stage's saved FC action.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function current(request, headers) {
  const res = await request.get("/v1/applications/current", {headers});
  expect(res.status(), (await res.text()).slice(0, 200)).toBe(200);
  return (await res.json()).tenant;
}

async function check(request, headers, stage, content) {
  const res = await request.post("/v1/checks/" + stage, {headers, data: {
    application_id: "demo2", content_format: "json", content: JSON.stringify(content)
  }});
  expect(res.status(), (await res.text()).slice(0, 200)).toBe(200);
  return res.json();
}

test("Input-only FC tab Save retains hidden Output action and executes both stages", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    resetRequired = true;
    const before = await current(request, headers);
    const configured = await request.post("/v1/tenants/current/checks", {
      headers, data: {revision: before.revision, changes: {
        comparisons: [{rule_id: "fc_stage_tab_invoice_le", left: "/invoice/refund",
          right: "/invoice/paid", operator: "le", value_type: "number",
          stages: ["input", "output"]}],
        input_check_cross_field: "flag", output_check_cross_field: "block"
      }}
    });
    expect(configured.status(), (await configured.text()).slice(0, 200)).toBe(200);
    const initialized = await current(request, headers);
    expect(initialized.revision).toBe(before.revision + 1);

    await connect(page);
    const input = page.locator("#tenant-check-input-cross_field");
    const output = page.locator("#tenant-check-output-cross_field");
    await expect(input).toHaveValue("flag");
    await expect(output).toHaveValue("block");

    await page.locator("#tenant-tab-input").click();
    await expect(page.locator("#tenant-panel")).toHaveAttribute("data-stage-focus", "input");
    await expect(input).toBeVisible();
    await expect(output).toBeHidden();
    await input.selectOption("block");

    await page.locator("#tenant-tab-output").click();
    await expect(input).toBeHidden();
    await expect(output).toBeVisible();
    await output.selectOption("flag");

    // Save while Output's column is hidden. Both stages must be included in
    // one authoritative revision-controlled tenant update, not separate forms.
    await page.locator("#tenant-tab-input").click();
    await expect(output).toBeHidden();
    expect((await current(request, headers)).revision).toBe(initialized.revision);
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current(request, headers);
    expect(saved.revision).toBe(initialized.revision + 1);
    expect(saved.input_checks.cross_field).toBe("block");
    expect(saved.output_checks.cross_field).toBe("flag");
    expect(saved.comparisons.map(rule => rule.rule_id)).toEqual(["fc_stage_tab_invoice_le"]);

    await connect(page);
    await page.locator("#tenant-tab-both").click();
    await expect(input).toHaveValue("block");
    await expect(output).toHaveValue("flag");
    await expect(input).toBeVisible();
    await expect(output).toBeVisible();

    const invalid = {invoice: {refund: 75, paid: 50}};
    const valid = {invoice: {refund: 25, paid: 50}};
    for (const [stage, action] of [["input", "block"], ["output", "flag"]]) {
      const bad = await check(request, headers, stage, invalid);
      expect(bad.evaluation_status).toBe("complete");
      expect(bad.effective_action).toBe(action);
      expect(bad.findings.map(f => f.rule_id)).toContain("business.fc_stage_tab_invoice_le");
      const clean = await check(request, headers, stage, valid);
      expect(clean.evaluation_status).toBe("complete");
      expect(clean.effective_action).toBe("allow");
      expect(clean.findings.map(f => f.rule_id)).not.toContain("business.fc_stage_tab_invoice_le");
    }

    // A previously saved, explicit Output-Off setting must remain Off when
    // Input is edited and saved with Output's column hidden. Hidden is NOT
    // auto, and a successful Input evaluation cannot re-enable Output rules.
    await page.locator("#tenant-tab-output").click();
    await output.selectOption("off");
    await page.locator("#tenant-tab-input").click();
    await expect(output).toBeHidden();
    await expect(page.locator("#tenant-stage-description")).toContainText(
      "Edits in either stage remain unsaved until Save checks");
    await expect(page.locator("#tenant-tab-input")).toHaveAttribute("aria-pressed", "true");
    await expect(page.locator("#tenant-tab-output")).toHaveAttribute("aria-pressed", "false");
    expect((await current(request, headers)).revision).toBe(saved.revision);
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const outputOff = await current(request, headers);
    expect(outputOff.revision).toBe(saved.revision + 1);
    expect(outputOff.input_checks.cross_field).toBe("block");
    expect(outputOff.output_checks.cross_field).toBe("off");
    expect(outputOff.comparisons).toEqual(saved.comparisons);

    await connect(page);
    await page.locator("#tenant-tab-input").click();
    await expect(input).toHaveValue("block");
    await expect(output).toBeHidden();
    await page.locator("#tenant-tab-output").click();
    await expect(output).toHaveValue("off");
    await page.locator("#tenant-tab-input").click();

    const blockedInput = await check(request, headers, "input", invalid);
    expect(blockedInput.evaluation_status).toBe("complete");
    expect(blockedInput.effective_action).toBe("block");
    expect(blockedInput.findings.map(f => f.rule_id)).toContain("business.fc_stage_tab_invoice_le");
    const allowedOutput = await check(request, headers, "output", invalid);
    expect(allowedOutput.evaluation_status).toBe("complete");
    expect(allowedOutput.effective_action).toBe("allow");
    expect(allowedOutput.findings.map(f => f.rule_id)).not.toContain("business.fc_stage_tab_invoice_le");
    expect((await current(request, headers)).revision).toBe(outputOff.revision);
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    }
  }
});
