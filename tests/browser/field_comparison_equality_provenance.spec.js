// Real dashboard/tenant/Engine regression: a strict cycle does not by itself
// prove field equality, even when all 3 fields share a directed SCC.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}
function rule(rule_id, left, right, operator, stages) {
  return {rule_id, left, right, operator, value_type: "number", stages};
}
async function importDraft(page, rules) {
  await page.locator("#comparison-bulk-json").fill(JSON.stringify(rules));
  await page.locator("#comparison-bulk-confirm").check();
  await page.locator("#comparison-bulk-import").click();
  await expect(page.locator("#comparison-draft-summary"))
    .toContainText(rules.length + " comparisons configured");
}
async function tenant(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status()).toBe(200);
  return (await response.json()).tenant;
}

test("distinguishes strict unsatisfiable cycles from actual forced equality in the saved tenant", async ({page, request}) => {
  const headers = await connect(page);
  let cleanup = false;
  try {
    expect((await request.post("/v1/tenants/current/reset", {headers, data: {}})).status()).toBe(200);
    cleanup = true;
    await connect(page);
    const before = await tenant(request, headers);
    const rules = [
      rule("in_ab", "/a", "/b", "lt", ["input"]),
      rule("in_bc", "/b", "/c", "le", ["input"]),
      rule("in_ca", "/c", "/a", "le", ["input"]),
      rule("in_ac", "/a", "/c", "ne", ["input"]),
      rule("out_de", "/d", "/e", "le", ["output"]),
      rule("out_ef", "/e", "/f", "le", ["output"]),
      rule("out_fd", "/f", "/d", "le", ["output"]),
      rule("out_df", "/d", "/f", "ne", ["output"])
    ];
    await page.locator("#comparison-bulk-panel > summary").click();
    await importDraft(page, rules);
    const warning = page.locator("#comparison-logic-warning");
    await expect(warning).toContainText("input has 1 impossible multi-field ordering cycle(s) involving 3+ fields");
    await expect(warning).toContainText("output has 1 not-equal comparison(s) contradicted by equality forced through 3+ fields");
    await expect(warning).not.toContainText("input has 1 not-equal");
    await expect(warning).not.toContainText("output has 1 impossible multi-field");
    await expect(warning).not.toContainText("/a");
    await expect(warning).not.toContainText("/d");
    expect((await tenant(request, headers)).revision).toBe(before.revision);
    expect((await tenant(request, headers)).comparisons).toEqual(before.comparisons);

    await page.locator("#tenant-check-input-cross_field").selectOption("flag");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await tenant(request, headers);
    expect(saved.revision).toBe(before.revision + 1);
    expect(saved.comparisons).toEqual(rules);
    await connect(page);
    await expect(page.locator("#comparison-logic-warning")).toContainText("input has 1 impossible multi-field");
    await expect(page.locator("#comparison-logic-warning")).not.toContainText("input has 1 not-equal");
    await expect(page.locator("#comparison-logic-warning")).toContainText("output has 1 not-equal");
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.in_ac"]')).toHaveCount(1);
    await expect(page.locator('#tenant-group-cross_field .rule-row[data-rule="business.out_df"]')).toHaveCount(1);

    const input = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo2", content_format: "json",
      content: JSON.stringify({a: 3, b: 2, c: 4})
    }});
    expect(input.status()).toBe(200);
    const inResult = await input.json();
    expect(inResult.evaluation_status).toBe("complete");
    expect(inResult.effective_action).toBe("flag");
    expect(inResult.findings.filter(x => x.rule_id.startsWith("business.")).map(x => x.rule_id))
      .toEqual(["business.in_ab", "business.in_ca"]);
    expect(await input.text()).not.toContain('"a": 3');

    const output = await request.post("/v1/checks/output", {headers, data: {
      application_id: "demo2", content_format: "json",
      content: JSON.stringify({d: 7, e: 7, f: 7})
    }});
    expect(output.status()).toBe(200);
    const outResult = await output.json();
    expect(outResult.evaluation_status).toBe("complete");
    expect(outResult.effective_action).toBe("block");
    expect(outResult.findings.filter(x => x.rule_id.startsWith("business.")).map(x => x.rule_id))
      .toEqual(["business.out_df"]);

    // Correct only the unsaved client draft; neither changes tenant rules nor bypasses enforcement.
    await page.locator("#comparison-bulk-panel > summary").click();
    const feasible = rules.map(x => x.rule_id === "in_ca" ? {...x, operator: "ge"} :
      x.rule_id === "out_fd" ? {...x, operator: "gt"} : x);
    await importDraft(page, feasible);
    await expect(page.locator("#comparison-logic-warning")).toBeHidden();
    expect((await tenant(request, headers)).revision).toBe(saved.revision);
    expect((await tenant(request, headers)).comparisons).toEqual(rules);
  } finally {
    if (cleanup) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
