// Actual authenticated tenant/dashboard/Engine acceptance for impossible and feasible
// field comparison JSON shapes. No mocked endpoints or weakened stage actions.
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
function rule(rule_id, left, right, value_type, stages) {
  return {rule_id, left, right, operator: "le", value_type, stages};
}
async function importDraft(page, definitions) {
  await page.locator("#comparison-bulk-json").fill(JSON.stringify(definitions));
  await page.locator("#comparison-bulk-confirm").check();
  await page.locator("#comparison-bulk-import").click();
  await expect(page.locator("#comparison-draft-summary"))
    .toContainText(definitions.length + " comparisons configured");
}
async function current(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status()).toBe(200);
  return (await response.json()).tenant;
}

test("dashboard warns on unsatisfiable same-stage scalar/object and mixed date/number, then saves valid staged rules", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    await connect(page);
    const initial = await current(request, headers);
    await page.locator("#comparison-bulk-panel > summary").click();

    const bad = [
      rule("type_numeric", "/shared", "/max_numeric", "number", ["input"]),
      rule("type_calendar", "/shared", "/max_calendar", "date", ["input"]),
      rule("path_scalar", "/root", "/outer_max", "number", ["output"]),
      rule("path_descendant", "/root/child", "/inner_max", "number", ["output"])
    ];
    await importDraft(page, bad);
    const warning = page.locator("#comparison-structure-warning");
    await expect(warning).toBeVisible();
    await expect(warning).toContainText("input has 1 field(s) used as both number and calendar date");
    await expect(warning).toContainText("output has 1 scalar/object JSON pointer conflict(s)");
    await expect(warning).not.toContainText("/shared");
    await expect(warning).not.toContainText("/root");
    // A staged draft must not mutate tenant state before revisioned Save.
    expect((await current(request, headers)).comparisons).toEqual(initial.comparisons);

    // One tenant may legitimately use a pointer as number in Input and date in
    // Output because these are separate documents. Similarly, /a~1b and /a/b
    // are different locations (slash escaping must not conflate them).
    const valid = [
      bad[0],
      {...bad[1], stages: ["output"]},
      bad[2],
      {...bad[3], left: "/nested/child"},
      rule("literal_slash", "/a~1b", "/max_slash", "number", ["input"]),
      rule("nested_slash", "/a/b", "/max_nested", "number", ["input"])
    ];
    await importDraft(page, valid);
    await expect(warning).toBeHidden();
    expect((await current(request, headers)).revision).toBe(initial.revision);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current(request, headers);
    expect(saved.revision).toBe(initial.revision + 1);
    expect(saved.comparisons).toEqual(valid);
    expect(saved.input_checks.cross_field).toBe("flag");
    expect(saved.output_checks.cross_field).toBe("block");

    await connect(page);
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.type_calendar"]')).toHaveCount(1);
    await expect(page.locator("#comparison-structure-warning")).toBeHidden();

    const inp = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo2", content_format: "json",
      content: JSON.stringify({shared: 9, max_numeric: 2, "a/b": 1,
        max_slash: 2, a: {b: 1}, max_nested: 2})
    }});
    expect(inp.status()).toBe(200);
    const gotInput = await inp.json();
    expect(gotInput.evaluation_status).toBe("complete");
    expect(gotInput.effective_action).toBe("flag");
    expect(gotInput.findings.filter(x => x.rule_id.startsWith("business.")).map(x => x.rule_id))
      .toEqual(["business.type_numeric"]);

    const out = await request.post("/v1/checks/output", {headers, data: {
      application_id: "demo2", content_format: "json",
      content: JSON.stringify({shared: "2026-01-01", max_calendar: "2026-12-31",
        root: 9, outer_max: 2, nested: {child: 9}, inner_max: 2})
    }});
    expect(out.status()).toBe(200);
    const gotOutput = await out.json();
    expect(gotOutput.evaluation_status).toBe("complete");
    expect(gotOutput.effective_action).toBe("block");
    expect(new Set(gotOutput.findings.filter(x => x.rule_id.startsWith("business.")).map(x => x.rule_id)))
      .toEqual(new Set(["business.path_scalar", "business.path_descendant"]));
    const outputBody = await out.text();
    expect(outputBody).not.toContain('"root":9');
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
