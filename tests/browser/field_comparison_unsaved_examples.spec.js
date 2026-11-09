// Customer-operable FC-031–050 protection against using examples for a stale
// persisted rule after a different definition has been staged but not saved.
// Real authenticated tenant, three actual dashboard editors and both Engine
// stages; no mocked app replies, provider references or tenant data changes.
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
  expect(response.status(), (await response.text()).slice(0, 160)).toBe(200);
  return (await response.json()).tenant;
}

test("edited arithmetic, identity and conversion drafts never offer stale Engine-certified Try samples", async ({page, request}) => {
  const headers = await connect(page);
  let resetDone = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetDone = true;
    const initial = await current(request, headers);
    const definitions = {
      comparisons: [],
      advanced_arithmetic: [{
        rule_id: "draft_math", kind: "invoice_total",
        fields: {subtotal: "/invoice/subtotal", discount: "/invoice/discount",
          tax: "/invoice/tax", shipping: "/invoice/shipping", total: "/invoice/total"},
        stages: ["input", "output"]
      }],
      advanced_identity: [{
        rule_id: "draft_identity", kind: "order_id_match",
        fields: {order_id: "/ids/order_id", shipment_order_id: "/ids/shipment_order_id"},
        stages: ["input", "output"], trusted_values: []
      }],
      advanced_conversion_time: [{
        rule_id: "draft_load", kind: "unit_load_kg_cap",
        fields: {load: "/load/load", load_unit: "/load/load_unit",
          max_kg: "/load/max_kg"},
        stages: ["input", "output"]
      }],
      input_check_cross_field: "flag", output_check_cross_field: "block"
    };
    const provision = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: initial.revision, changes: definitions
    }});
    expect(provision.status(), (await provision.text()).slice(0, 200)).toBe(200);
    const persisted = await current(request, headers);
    expect(persisted.revision).toBe(initial.revision + 1);
    for (const id of ["draft_math", "draft_identity", "draft_load"]) {
      const row = persisted.record_rules.find(x => x.rule_id === "business." + id);
      expect(row, id).toBeTruthy();
      expect(typeof row.example, id).toBe("string");
      expect(typeof row.look_alike, id).toBe("string");
    }

    await connect(page);
    const controls = [
      ["arithmetic", "draft_math", "#arithmetic-field-total", "/invoice/total_v2"],
      ["identity", "draft_identity", "#identity-field-shipment_order_id", "/ids/shipment_order_id_v2"],
      ["conversion", "draft_load", "#conversion-field-load", "/load/load_v2"]
    ];
    for (const [family, id, field, pointer] of controls) {
      await expect(page.getByRole("button", {name: "Try violating input " + family + " " + id}))
        .toHaveCount(1);
      await page.getByRole("button", {name: "Edit " + family + " " + id}).click();
      await page.locator(field).fill(pointer);
      await page.locator("#" + family + "-apply").click();
      await expect(page.locator('[data-' + family + '-id="' + id + '"]'))
        .toContainText("Unsaved " + (family === "conversion" ? "conversion/time" : family) + " rule changes");
      for (const stage of ["input", "output"]) {
        for (const kind of ["violating", "benign"]) {
          await expect(page.getByRole("button", {name:
            "Try " + kind + " " + stage + " " + family + " " + id})).toHaveCount(0);
        }
      }
    }
    // The browser stages the edits but must not mutate the saved policy or
    // advertise samples belonging to an outdated definition.
    const untouched = await current(request, headers);
    expect(untouched.revision).toBe(persisted.revision);
    expect(untouched.advanced_arithmetic).toEqual(persisted.advanced_arithmetic);
    expect(untouched.advanced_identity).toEqual(persisted.advanced_identity);
    expect(untouched.advanced_conversion_time).toEqual(persisted.advanced_conversion_time);

    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const updated = await current(request, headers);
    expect(updated.revision).toBe(persisted.revision + 1);
    expect(updated.advanced_arithmetic[0].fields.total).toBe("/invoice/total_v2");
    expect(updated.advanced_identity[0].fields.shipment_order_id).toBe("/ids/shipment_order_id_v2");
    expect(updated.advanced_conversion_time[0].fields.load).toBe("/load/load_v2");

    await connect(page);
    for (const [family, id] of controls) {
      await expect(page.getByRole("button", {name: "Try violating input " + family + " " + id}))
        .toHaveCount(1);
      await expect(page.getByRole("button", {name: "Try benign output " + family + " " + id}))
        .toHaveCount(1);
    }
    await page.getByRole("button", {name: "Try violating input arithmetic draft_math"}).click();
    expect(JSON.parse(await page.locator("#input-content").inputValue()).invoice)
      .toHaveProperty("total_v2");

    const violating = {
      invoice: {subtotal: 100, discount: 10, tax: 8, shipping: 5, total_v2: 110},
      ids: {order_id: "ORDER1", shipment_order_id_v2: "ORDER2"},
      load: {load_v2: 1500, load_unit: "lb", max_kg: 600}
    };
    const benign = {
      invoice: {subtotal: 100, discount: 10, tax: 8, shipping: 5, total_v2: 103},
      ids: {order_id: "ORDER1", shipment_order_id_v2: "ORDER1"},
      load: {load_v2: 1000, load_unit: "lb", max_kg: 600}
    };
    const expected = ["business.draft_identity", "business.draft_load", "business.draft_math"];
    for (const stage of ["input", "output"]) {
      for (const [payload, wanted] of [[violating, expected], [benign, []]]) {
        const result = await request.post("/v1/checks/" + stage, {headers, data: {
          application_id: "demo2", content_format: "json", content: JSON.stringify(payload)
        }});
        expect(result.status(), (await result.text()).slice(0, 160)).toBe(200);
        const body = await result.json();
        expect(body.evaluation_status).toBe("complete");
        expect(body.findings.filter(x => expected.includes(x.rule_id))
          .map(x => x.rule_id).sort()).toEqual(wanted);
        expect(body.effective_action).toBe(wanted.length ?
          (stage === "input" ? "flag" : "block") : "allow");
      }
    }
    expect((await current(request, headers)).revision).toBe(updated.revision);
  } finally {
    if (resetDone) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    }
  }
});
