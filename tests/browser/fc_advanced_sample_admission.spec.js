// Real authenticated FC-031–049 customer sample admission: no mocked GET,
// no parallel engine and no mutation of stored policy when samples are withheld.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator('#comparison-configuration').evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function tenant(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status(), (await response.text()).slice(0, 160)).toBe(200);
  return (await response.json()).tenant;
}

function portfolio(deep) {
  const root = "/deep" + (deep ? "/x".repeat(33) : "");
  return {
    advanced_arithmetic: [{
      rule_id: "sample_arith", kind: "invoice_total",
      fields: Object.fromEntries(["subtotal", "discount", "tax", "shipping", "total"]
        .map(name => [name, root + "/invoice/" + name])),
      stages: ["input", "output"],
    }],
    advanced_identity: [{
      rule_id: "sample_identity", kind: "order_id_match",
      fields: {order_id: root + "/ids/order_id",
        shipment_order_id: root + "/ids/shipment_order_id"},
      stages: ["input", "output"],
    }],
    comparisons: [],
    input_check_cross_field: "flag",
    output_check_cross_field: "block",
  };
}

test("deep valid tenant rules persist but unusable arithmetic and identity Try samples disappear", async ({page, request}) => {
  const headers = await connect(page);
  let resetDone = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetDone = true;
    const initial = await tenant(request, headers);
    const deep = portfolio(true);
    const saved = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: initial.revision, changes: deep,
    }});
    expect(saved.status(), (await saved.text()).slice(0, 200)).toBe(200);
    const current = await tenant(request, headers);
    expect(current.revision).toBe(initial.revision + 1);
    expect(current.advanced_arithmetic).toEqual(deep.advanced_arithmetic);
    // The authoritative model normalizes non-reference identity rules with
    // an explicitly empty trusted-values set; that is not a leaked reference.
    expect(current.advanced_identity).toHaveLength(1);
    expect(current.advanced_identity[0]).toMatchObject(deep.advanced_identity[0]);
    expect(current.advanced_identity[0].trusted_values).toEqual([]);
    for (const id of ["sample_arith", "sample_identity"]) {
      const row = current.record_rules.find(x => x.rule_id === "business." + id);
      expect(row).toBeTruthy();
      expect(row.example).toBeNull();
      expect(row.look_alike).toBeNull();
      expect(row.example_unavailable.code).toBe("sample_unavailable");
      expect(row.look_alike_unavailable.code).toBe("sample_unavailable");
    }

    await connect(page);
    await expect(page.locator('[data-arithmetic-id="sample_arith"]')).toHaveCount(1);
    await expect(page.locator('[data-identity-id="sample_identity"]')).toHaveCount(1);
    // An absent Try button must not leave the customer guessing why.
    for (const selector of ['[data-arithmetic-id="sample_arith"]',
                            '[data-identity-id="sample_identity"]']) {
      await expect(page.locator(selector)).toContainText("strict JSON admission");
      // The saved rule exists, but its effective execution still depends on
      // the parent stage check and individual rule action. A missing example
      // must never claim an Off rule unconditionally executes.
      await expect(page.locator(selector)).toContainText("saved definition remains subject to stage check and rule actions");
      await expect(page.locator(selector)).toContainText("input, output");
      await expect(page.locator(selector)).toContainText("shallower JSON pointers");
    }
    for (const family of ["arithmetic", "identity"]) {
      for (const kind of ["violating", "benign"]) {
        for (const stage of ["input", "output"]) {
          await expect(page.getByRole("button", {name:
            new RegExp("Try " + kind + " " + stage + " " + family + " sample_")})).toHaveCount(0);
        }
      }
    }
    await expect(page.locator('tr.rule-row[data-rule="business.sample_arith"]')).toHaveCount(1);
    await expect(page.locator('tr.rule-row[data-rule="business.sample_identity"]')).toHaveCount(1);
    expect((await tenant(request, headers)).revision).toBe(current.revision);

    // The exact same customer rule IDs and stage actions with admissible
    // pointers should again expose BOTH Engine-certified Try controls.
    const shallow = portfolio(false);
    const changed = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: current.revision, changes: shallow,
    }});
    expect(changed.status(), (await changed.text()).slice(0, 200)).toBe(200);
    const after = await tenant(request, headers);
    expect(after.revision).toBe(current.revision + 1);
    for (const id of ["sample_arith", "sample_identity"]) {
      const row = after.record_rules.find(x => x.rule_id === "business." + id);
      expect(typeof row.example).toBe("string");
      expect(typeof row.look_alike).toBe("string");
    }
    await connect(page);
    for (const selector of ['[data-arithmetic-id="sample_arith"]',
                            '[data-identity-id="sample_identity"]']) {
      await expect(page.locator(selector)).not.toContainText("strict JSON admission");
    }
    await expect(page.getByRole("button", {name: "Try violating input arithmetic sample_arith"}))
      .toHaveCount(1);
    await expect(page.getByRole("button", {name: "Try benign output arithmetic sample_arith"}))
      .toHaveCount(1);
    await expect(page.getByRole("button", {name: "Try violating output identity sample_identity"}))
      .toHaveCount(1);
    await expect(page.getByRole("button", {name: "Try benign input identity sample_identity"}))
      .toHaveCount(1);

    // Exercise the actual customer Try -> playground -> shared Engine path.
    // The selected rule's synthetic witness is not necessarily a clean
    // whole-tenant witness: other enabled rules may also find violations.
    const sampleFor = (id, kind) => {
      const rule = after.record_rules.find(x => x.rule_id === "business." + id);
      expect(rule).toBeTruthy();
      return kind === "violating" ? rule.example : rule.look_alike;
    };
    const tryAndRun = async (stage, family, id, kind) => {
      await page.getByRole("button", {name: "Try " + kind + " " + stage + " " + family + " " + id}).click();
      await expect(page.locator("#" + stage + "-format")).toHaveValue("json");
      expect(await page.locator("#" + stage + "-content").inputValue()).toBe(sampleFor(id, kind));
      const pending = page.waitForResponse(r => r.url().endsWith("/v1/checks/" + stage) &&
        r.request().method() === "POST");
      await page.locator("#run-" + stage).click();
      const response = await pending;
      expect(response.status(), (await response.text()).slice(0, 160)).toBe(200);
      const report = await response.json();
      const selected = report.findings.filter(x => x.rule_id === "business." + id);
      expect(selected.length > 0, "Selected rule must match its claimed " + kind + " example")
        .toBe(kind === "violating");
    };
    await tryAndRun("input", "arithmetic", "sample_arith", "violating");
    await tryAndRun("input", "identity", "sample_identity", "benign");
    // A complete, benign input (rather than one rule's isolated witness)
    // authorizes the Output stage. A previous incomplete sample cannot
    // silently grant Output access.
    const cleanDocument = {deep: {invoice: {
      subtotal: 100, discount: 10, tax: 8, shipping: 5, total: 103
    }, ids: {order_id: "ORDER1", shipment_order_id: "ORDER1"}}};
    await page.locator("#input-format").selectOption("json");
    await page.locator("#input-content").fill(JSON.stringify(cleanDocument));
    const completeInput = page.waitForResponse(r => r.url().endsWith("/v1/checks/input") &&
      r.request().method() === "POST");
    await page.locator("#run-input").click();
    const completeReport = await (await completeInput).json();
    expect(completeReport.evaluation_status).toBe("complete");
    expect(completeReport.effective_action).toBe("allow");
    await expect(page.locator("#run-output")).toBeEnabled();
    await tryAndRun("output", "identity", "sample_identity", "violating");
    await tryAndRun("output", "arithmetic", "sample_arith", "benign");
    expect((await tenant(request, headers)).revision).toBe(after.revision);

    const good = {deep: {invoice: {subtotal: 100, discount: 10, tax: 8, shipping: 5, total: 103},
      ids: {order_id: "ORDER1", shipment_order_id: "ORDER1"}}};
    const bad = structuredClone(good);
    bad.deep.invoice.total = 110;
    bad.deep.ids.shipment_order_id = "ORDER2";
    for (const stage of ["input", "output"]) {
      for (const [document, expected] of [[good, []],
        [bad, ["business.sample_arith", "business.sample_identity"]]]) {
        const response = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo2", content_format: "json", content: JSON.stringify(document),
        }});
        expect(response.status()).toBe(200);
        const result = await response.json();
        expect(result.evaluation_status).toBe("complete");
        expect(result.findings.filter(x => x.rule_id.startsWith("business."))
          .map(x => x.rule_id).sort()).toEqual(expected);
        expect(result.effective_action).toBe(expected.length ?
          (stage === "input" ? "flag" : "block") : "allow");
      }
    }
    expect((await tenant(request, headers)).revision).toBe(after.revision);
  } finally {
    if (resetDone) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    }
  }
});
