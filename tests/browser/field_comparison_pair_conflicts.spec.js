// Authenticated tenant dashboard/Engine acceptance for contradictory pairwise operators.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}
async function current(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status()).toBe(200);
  return (await response.json()).tenant;
}
function comparison(rule_id, left, right, operator, value_type, stages) {
  return {rule_id, left, right, operator, value_type, stages};
}
async function stageDraft(page, rules) {
  await page.locator("#comparison-bulk-json").fill(JSON.stringify(rules));
  await page.locator("#comparison-bulk-confirm").check();
  await page.locator("#comparison-bulk-import").click();
  await expect(page.locator("#comparison-draft-summary"))
    .toContainText(rules.length + " comparisons configured");
}

test("opposite-operand pair contradictions warn separately by stage with real tenant save, reload and Engine findings", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    await connect(page);
    const initial = await current(request, headers);
    await page.locator("#comparison-bulk-panel > summary").click();
    const conflicting = [
      comparison("inp_less", "/a", "/b", "lt", "number", ["input"]),
      comparison("inp_reverse", "/b", "/a", "le", "number", ["input"]),
      comparison("out_equal", "/stamp", "/other", "eq", "date", ["output"]),
      comparison("out_different", "/other", "/stamp", "ne", "date", ["output"]),
      // Reversed but consistent relationships must NOT warn.
      comparison("out_greater", "/unit", "/limit", "ge", "number", ["output"]),
      comparison("out_reverse", "/limit", "/unit", "le", "number", ["output"]),
      // One literal slash segment versus a nested path, no pair alias.
      comparison("inp_escaped", "/slash~1field", "/nest/value", "le", "number", ["input"])
    ];
    await stageDraft(page, conflicting);
    const warning = page.locator("#comparison-logic-warning");
    await expect(warning).toBeVisible();
    await expect(warning).toContainText("input has 1 pair(s) of fields with mutually incompatible");
    await expect(warning).toContainText("output has 1 pair(s) of fields with mutually incompatible");
    await expect(warning).not.toContainText("/stamp");
    await expect(warning).not.toContainText("/slash~1field");
    await expect(page.locator("#comparison-structure-warning")).toBeHidden();
    expect((await current(request, headers)).revision).toBe(initial.revision);
    expect((await current(request, headers)).comparisons).toEqual(initial.comparisons);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current(request, headers);
    expect(saved.revision).toBe(initial.revision + 1);
    expect(saved.comparisons).toEqual(conflicting);
    await connect(page);
    await expect(page.locator("#comparison-logic-warning")).toContainText("input has 1 pair(s)");
    await expect(page.locator("#comparison-logic-warning")).toContainText("output has 1 pair(s)");
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.inp_less"]')).toHaveCount(1);
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.out_different"]')).toHaveCount(1);

    const input = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo2", content_format: "json",
      content: JSON.stringify({a: 4, b: 2, "slash/field": 1, nest: {value: 2}})
    }});
    expect(input.status()).toBe(200);
    const gotInput = await input.json();
    expect(gotInput.evaluation_status).toBe("complete");
    expect(gotInput.effective_action).toBe("flag");
    expect(gotInput.findings.filter(x => x.rule_id.startsWith("business.")).map(x => x.rule_id))
      .toEqual(["business.inp_less"]);

    const output = await request.post("/v1/checks/output", {headers, data: {
      application_id: "demo2", content_format: "json",
      content: JSON.stringify({stamp: "2026-11-08", other: "2026-11-08", unit: 7, limit: 4})
    }});
    expect(output.status()).toBe(200);
    const gotOutput = await output.json();
    expect(gotOutput.evaluation_status).toBe("complete");
    expect(gotOutput.effective_action).toBe("block");
    expect(gotOutput.findings.filter(x => x.rule_id.startsWith("business.")).map(x => x.rule_id))
      .toEqual(["business.out_different"]);
    expect(await output.text()).not.toContain('"stamp":"2026-11-08"');

    // Repair only the draft; the stored record retains its exact revision.
    await page.locator("#comparison-bulk-panel > summary").click();
    await stageDraft(page, conflicting.map(row => {
      if (row.rule_id === "inp_reverse") return {...row, operator: "gt"};
      if (row.rule_id === "out_different") return {...row, operator: "eq"};
      return row;
    }));
    await expect(page.locator("#comparison-logic-warning")).toBeHidden();
    expect((await current(request, headers)).revision).toBe(saved.revision);
    expect((await current(request, headers)).comparisons).toEqual(conflicting);
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
