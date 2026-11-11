// Real tenant/dashboard/Engine proof for stage-aware self-comparison logic warnings.
// All warnings are advisory, and the actual authoritative tenant record is
// unchanged until explicit revisioned Save. No fake API/Engine responses.
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
  const res = await request.get("/v1/applications/current", {headers});
  expect(res.status()).toBe(200);
  return (await res.json()).tenant;
}
function rule(rule_id, left, right, operator, stages) {
  return {rule_id, left, right, operator, value_type: "number", stages};
}
async function stage(page, rules) {
  await page.locator("#comparison-bulk-json").fill(JSON.stringify(rules));
  await page.locator("#comparison-bulk-confirm").check();
  await page.locator("#comparison-bulk-import").click();
  await expect(page.locator("#comparison-draft-summary"))
    .toContainText(rules.length + " comparisons configured");
}

test("self-pointer stage warnings distinguish impossible/vacuous rules, then actual saved tenant Engine findings", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    await connect(page);
    const before = await current(request, headers);
    await page.locator("#comparison-bulk-panel > summary").click();
    const definitions = [
      rule("self_input_strict", "/same", "/same", "lt", ["input"]),
      rule("self_output_different", "/same", "/same", "ne", ["output"]),
      rule("self_both_always_holds", "/same", "/same", "le", ["input", "output"]),
      // A slash inside a field is NOT a JSON Pointer path separator.
      rule("escaped_distinct", "/a~1b", "/a/b", "le", ["input", "output"])
    ];
    await stage(page, definitions);
    const warning = page.locator("#comparison-logic-warning");
    await expect(warning).toBeVisible();
    await expect(warning).toContainText("input has 1 self-comparison(s) that always violate");
    await expect(warning).toContainText("output has 1 self-comparison(s) that always violate");
    await expect(warning).toContainText("input has 1 self-comparison(s) that cannot detect");
    await expect(warning).toContainText("output has 1 self-comparison(s) that cannot detect");
    await expect(warning).not.toContainText("/same");
    await expect(page.locator("#comparison-structure-warning")).toBeHidden();
    expect((await current(request, headers)).revision).toBe(before.revision);
    expect((await current(request, headers)).comparisons).toEqual(before.comparisons);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current(request, headers);
    expect(saved.revision).toBe(before.revision + 1);
    expect(saved.comparisons).toEqual(definitions);
    await connect(page);
    await expect(page.locator("#comparison-logic-warning")).toBeVisible();
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.self_input_strict"]')).toHaveCount(1);

    for (const [stageName, ruleId, outcome] of [
      ["input", "business.self_input_strict", "flag"],
      ["output", "business.self_output_different", "block"]
    ]) {
      const response = await request.post("/v1/checks/" + stageName, {headers, data: {
        application_id: "demo2", content_format: "json",
        content: JSON.stringify({same: 4, "a/b": 1, a: {b: 2}})
      }});
      expect(response.status()).toBe(200);
      const got = await response.json();
      expect(got.evaluation_status).toBe("complete");
      expect(got.effective_action).toBe(outcome);
      const ids = got.findings.filter(f => f.rule_id.startsWith("business.")).map(f => f.rule_id);
      expect(ids).toEqual([ruleId]);
      expect(ids).not.toContain("business.self_both_always_holds");
      expect(ids).not.toContain("business.escaped_distinct");
      expect(await response.text()).not.toContain('"same":4');
    }

    // Fix both operands without persistence; warnings disappear in the editor,
    // while the committed record and tenant revision remain authoritative.
    await page.locator("#comparison-bulk-panel > summary").click();
    await stage(page, [
      rule("valid_input", "/same", "/other", "lt", ["input"]),
      rule("valid_output", "/same", "/other", "ne", ["output"])
    ]);
    await expect(page.locator("#comparison-logic-warning")).toBeHidden();
    expect((await current(request, headers)).revision).toBe(saved.revision);
    expect((await current(request, headers)).comparisons).toEqual(definitions);
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
