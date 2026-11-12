// Exact customer acceptance for 500 rules in ONE advanced arithmetic family.
// Uses the authenticated tenant dashboard, revisioned Save and shared Engine.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(node => { node.open = true; });
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

test("500 arithmetic definitions stage atomically with cross-family duplicate protection and execute on both stages", async ({page, request}) => {
  let headers = await connect(page);
  let resetRequired = false;
  const current = async () => {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    headers = await connect(page);
    const before = await current();
    expect(before.advanced_arithmetic).toEqual([]);

    const rules = Array.from({length: 500}, (_, i) => {
      const n = String(i).padStart(3, "0");
      return {rule_id: "bulk_math_500_" + n, kind: "manufacturing_tolerance",
        fields: {measured: "/m" + n + "/a", nominal: "/m" + n + "/b",
          tolerance: "/m" + n + "/t"},
        stages: ["input", "output"]};
    });
    await page.locator("#arithmetic-bulk-panel").evaluate(node => { node.open = true; });
    const json = page.locator("#arithmetic-bulk-json");
    const stage = page.locator("#arithmetic-bulk-import");

    // An unsaved ordinary comparison must reserve its rule ID even though
    // it is not yet part of record_rules. No partial bulk import is allowed.
    const collidingId = rules[499].rule_id;
    await page.locator("#comparison-id").fill(collidingId);
    await page.locator("#comparison-left").fill("/other/a");
    await page.locator("#comparison-right").fill("/other/b");
    await page.locator("#comparison-apply").click();
    await expect(page.locator("#comparison-draft-summary")).toContainText("1 added");
    await json.fill(JSON.stringify(rules));
    await page.locator("#arithmetic-bulk-confirm").check();
    await stage.click();
    await expect(page.locator("#arithmetic-bulk-status")).toContainText(
      "duplicate or reserved rule_id");
    await expect(page.locator("#arithmetic-draft-summary"))
      .toContainText("Saved arithmetic rules: 0. No pending changes.");
    expect((await current()).revision).toBe(before.revision);
    await page.getByRole("button", {name: "Remove comparison " + collidingId}).click();
    await expect(page.locator("#comparison-draft-summary")).toContainText("No pending changes");

    await page.locator("#arithmetic-bulk-confirm").check();
    await stage.click();
    await expect(page.locator("#arithmetic-draft-summary"))
      .toContainText("500 added, 0 updated, 0 removed");
    await expect(page.locator("#arithmetic-filter-status")).toContainText("Showing 500 of 500");
    expect((await current()).advanced_arithmetic).toEqual([]);
    expect((await current()).revision).toBe(before.revision);

    // The last malformed rule cannot erase any of the 500 previously staged
    // definitions. The client never persists partial or failed imports.
    const invalid = structuredClone(rules);
    invalid[499].fields.tolerance = invalid[499].fields.measured;
    await json.fill(JSON.stringify(invalid));
    await page.locator("#arithmetic-bulk-confirm").check();
    await stage.click();
    await expect(page.locator("#arithmetic-bulk-status"))
      .toContainText("distinct and nonoverlapping");
    await expect(page.locator("#arithmetic-draft-summary"))
      .toContainText("500 added, 0 updated, 0 removed");
    expect((await current()).revision).toBe(before.revision);
    await page.locator("#arithmetic-bulk-export").click();
    expect(JSON.parse(await json.inputValue())).toEqual(rules);

    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await current();
    expect(saved.revision).toBe(before.revision + 1);
    expect(saved.advanced_arithmetic).toEqual(rules);
    expect(saved.comparisons).toEqual(before.comparisons);
    await expect(page.locator("#arithmetic-draft-summary"))
      .toContainText("Saved arithmetic rules: 500. No pending changes.");
    await expect(page.locator('tr.rule-row[data-rule^="business.bulk_math_500_"]'))
      .toHaveCount(500);
    await page.locator("#arithmetic-filter").fill("bulk_math_500_499");
    await expect(page.locator("#arithmetic-filter-status")).toContainText("Showing 1 of 500");
    expect((await current()).revision).toBe(saved.revision);

    const good = {};
    for (let i = 0; i < 500; i++) {
      const n = String(i).padStart(3, "0");
      good["m" + n] = {a: 100, b: 100, t: 2};
    }
    const bad = structuredClone(good);
    bad.m000.a = 105;
    bad.m499.a = 105;
    expect(JSON.stringify(bad).length).toBeLessThan(32000);
    const expected = ["business.bulk_math_500_000", "business.bulk_math_500_499"];
    for (const stageName of ["input", "output"]) {
      for (const [document, violating] of [[bad, true], [good, false]]) {
        const response = await request.post("/v1/checks/" + stageName, {headers, data: {
          application_id: "demo8", content_format: "json",
          content: JSON.stringify(document)
        }});
        expect(response.status()).toBe(200);
        const outcome = await response.json();
        expect(outcome.evaluation_status).toBe("complete");
        const matched = outcome.findings.filter(f =>
          f.rule_id.startsWith("business.bulk_math_500_")).map(f => f.rule_id).sort();
        expect(matched).toEqual(violating ? expected : []);
        if (violating) expect(outcome.effective_action).toBe("block");
        else if (outcome.effective_action !== "allow") {
          // Other enabled checks may legitimately report stronger findings.
          expect(outcome.findings.filter(f =>
            !f.rule_id.startsWith("business.bulk_math_500_")).length).toBeGreaterThan(0);
        }
      }
    }
    expect((await current()).revision).toBe(saved.revision);
  } finally {
    if (resetRequired) {
      const clean = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(clean.status()).toBe(200);
    }
  }
});
