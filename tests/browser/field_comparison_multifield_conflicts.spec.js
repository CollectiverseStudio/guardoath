// End-to-end customer acceptance for >2-field comparisons, not mocked warnings.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  // Explicitly expand optional tools for this advanced-editor acceptance test.
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
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
function rule(rule_id, left, right, operator, value_type, stages) {
  return {rule_id, left, right, operator, value_type, stages};
}
async function stageDraft(page, rules) {
  await page.locator("#comparison-bulk-json").fill(JSON.stringify(rules));
  await page.locator("#comparison-bulk-confirm").check();
  await page.locator("#comparison-bulk-import").click();
  await expect(page.locator("#comparison-draft-summary"))
    .toContainText(rules.length + " comparisons configured");
}

test("multi-field strict cycles and indirect not-equal conflicts warn, save, and evaluate on real tenant stages", async ({page, request}) => {
  const headers = await connect(page);
  let cleanup = false;
  try {
    expect((await request.post("/v1/tenants/current/reset", {headers, data: {}})).status()).toBe(200);
    cleanup = true;
    await connect(page);
    const before = await current(request, headers);
    const rules = [
      rule("in_ab", "/a", "/b", "lt", "number", ["input"]),
      rule("in_bc", "/b", "/c", "le", "number", ["input"]),
      rule("in_ca", "/c", "/a", "le", "number", ["input"]),
      rule("out_de", "/d", "/e", "eq", "date", ["output"]),
      rule("out_ef", "/e", "/f", "eq", "date", ["output"]),
      rule("out_df", "/d", "/f", "ne", "date", ["output"])
    ];
    await page.locator("#comparison-bulk-panel > summary").click();
    await stageDraft(page, rules);
    const warning = page.locator("#comparison-logic-warning");
    await expect(warning).toContainText("input has 1 impossible multi-field ordering cycle(s) involving 3+ fields");
    await expect(warning).toContainText("output has 1 not-equal comparison(s) contradicted by equality forced through 3+ fields");
    await expect(warning).not.toContainText("/a");
    await expect(warning).not.toContainText("/d");
    await expect(page.locator("#comparison-structure-warning")).toBeHidden();
    expect((await current(request, headers)).comparisons).toEqual(before.comparisons);
    expect((await current(request, headers)).revision).toBe(before.revision);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current(request, headers);
    expect(saved.comparisons).toEqual(rules);
    expect(saved.revision).toBe(before.revision + 1);
    await connect(page);
    await expect(page.locator("#comparison-logic-warning")).toContainText("input has 1 impossible multi-field");
    await expect(page.locator("#comparison-logic-warning")).toContainText("output has 1 not-equal");
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.in_ab"]')).toHaveCount(1);
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.out_df"]')).toHaveCount(1);

    const input = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo2", content_format: "json",
      content: JSON.stringify({a: 3, b: 2, c: 4})
    }});
    expect(input.status()).toBe(200);
    const inputData = await input.json();
    expect(inputData.evaluation_status).toBe("complete");
    expect(inputData.effective_action).toBe("flag");
    expect(inputData.findings.filter(x => x.rule_id.startsWith("business.")).map(x => x.rule_id))
      .toEqual(["business.in_ab", "business.in_ca"]);
    expect(await input.text()).not.toContain('"a": 3');

    const output = await request.post("/v1/checks/output", {headers, data: {
      application_id: "demo2", content_format: "json",
      content: JSON.stringify({d: "2026-11-08", e: "2026-11-08", f: "2026-11-08"})
    }});
    expect(output.status()).toBe(200);
    const outputData = await output.json();
    expect(outputData.evaluation_status).toBe("complete");
    expect(outputData.effective_action).toBe("block");
    expect(outputData.findings.filter(x => x.rule_id.startsWith("business.")).map(x => x.rule_id))
      .toEqual(["business.out_df"]);

    // A satisfiable draft removes the warning WITHOUT mutating the saved policy.
    await page.locator("#comparison-bulk-panel > summary").click();
    const benign = rules.map(x =>
      x.rule_id === "in_ab" ? {...x, operator: "le"} :
      x.rule_id === "out_df" ? {...x, operator: "eq"} : x);
    await stageDraft(page, benign);
    await expect(page.locator("#comparison-logic-warning")).toBeHidden();
    expect((await current(request, headers)).revision).toBe(saved.revision);
    expect((await current(request, headers)).comparisons).toEqual(rules);
  } finally {
    if (cleanup) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
