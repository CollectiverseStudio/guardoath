// Customer-facing Field Comparisons sample dropdown must produce real array JSON
// consumed by the same tenant and shared Engine, not dictionary-shaped fake examples.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function chooseRuleExample(page, stage, id, kind) {
  await page.locator("#" + stage + "-example-check").selectOption("cross_field");
  const option = page.locator("#" + stage + "-example optgroup")
    .filter({has: page.locator("option")})
    .locator("option").filter({hasText: "business." + id});
  // Each rule has both reported and look-alike samples, in that order.
  const value = await (kind === "reported" ? option.first() : option.last()).getAttribute("value");
  expect(value).toBeTruthy();
  await page.locator("#" + stage + "-example").selectOption(value);
  const doc = JSON.parse(await page.locator("#" + stage + "-content").inputValue());
  expect(Array.isArray(doc.order.items)).toBe(true);
  expect(doc.order.items).toHaveLength(2);
  return doc;
}

test("dashboard generated array-path examples run with genuine tenant stage actions and all findings", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const initialRes = await request.get("/v1/applications/current", {headers});
    expect(initialRes.status()).toBe(200);
    const initial = (await initialRes.json()).tenant;
    const definitions = ["zero", "one"].map((id, i) => ({
      rule_id: "array_" + id, left: "/order/items/" + i + "/price",
      right: "/order/limit", operator: "le", value_type: "number",
      stages: ["input", "output"]
    }));
    const save = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: initial.revision, changes: {
        comparisons: definitions,
        input_check_cross_field: "flag", output_check_cross_field: "block"
      }
    }});
    expect(save.status()).toBe(200);
    const saved = (await (await request.get("/v1/applications/current", {headers})).json()).tenant;
    expect(saved.revision).toBe(initial.revision + 1);
    expect(saved.comparisons).toEqual(definitions);
    for (const id of ["array_zero", "array_one"]) {
      const meta = saved.record_rules.find(rule => rule.rule_id === "business." + id);
      expect(meta).toBeTruthy();
      expect(meta.example_unavailable).toBeNull();
      expect(meta.look_alike_unavailable).toBeNull();
      expect(Array.isArray(JSON.parse(meta.example).order.items)).toBe(true);
    }
    await connect(page);
    const example = await chooseRuleExample(page, "input", "array_zero", "reported");
    expect(typeof example.order.items[0].price).toBe("number");
    await page.locator("#run-input").click();
    await expect(page.locator('#input-results button[aria-label="Review input rule settings for business.array_zero"]')).toHaveCount(1);
    const input = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo2", content_format: "json", content: JSON.stringify(example)
    }});
    expect(input.status()).toBe(200);
    const inData = await input.json();
    expect(inData.evaluation_status).toBe("complete");
    expect(inData.effective_action).toBe("flag");
    expect(inData.findings.filter(f => f.rule_id.startsWith("business.")).map(f => f.rule_id))
      .toEqual(["business.array_zero"]);

    await chooseRuleExample(page, "output", "array_one", "reported");
    await page.locator("#run-output").click();
    await expect(page.locator('#output-results button[aria-label="Review output rule settings for business.array_one"]')).toHaveCount(1);
    const outputContent = await page.locator("#output-content").inputValue();
    const output = await request.post("/v1/checks/output", {headers, data: {
      application_id: "demo2", content_format: "json", content: outputContent
    }});
    expect(output.status()).toBe(200);
    const outData = await output.json();
    expect(outData.evaluation_status).toBe("complete");
    expect(outData.effective_action).toBe("block");
    expect(outData.findings.filter(f => f.rule_id.startsWith("business.")).map(f => f.rule_id))
      .toEqual(["business.array_one"]);
    await expect(page.locator("#answer")).toBeHidden();
    const unchanged = (await (await request.get("/v1/applications/current", {headers})).json()).tenant;
    expect(unchanged.comparisons).toEqual(definitions);
    expect(unchanged.revision).toBe(saved.revision);
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
