// Real authenticated dashboard regression: bulk replacement acknowledgement must
// be reconfirmed after text edits in ALL four Field Comparison families.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function tenant(request, headers) {
  const res = await request.get("/v1/applications/current", {headers});
  expect(res.status(), (await res.text()).slice(0, 200)).toBe(200);
  return (await res.json()).tenant;
}

test("ordinary/arithmetic/identity/conversion bulk edits invalidate replacement consent before revisioned Save", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    resetRequired = true;
    await connect(page);
    const before = await tenant(request, headers);
    for (const config of [
      {prefix: "comparison", sample: "comparison-bulk-example", family: "comparisons", editor: "comparisonEditor"},
      {prefix: "arithmetic", sample: "arithmetic-bulk-example", family: "advanced_arithmetic", editor: "arithmeticEditor"},
      {prefix: "identity", sample: "identity-bulk-sample", family: "advanced_identity", editor: "identityEditor"},
      {prefix: "conversion", sample: "conversion-bulk-sample", family: "advanced_conversion_time", editor: "conversionEditor"},
    ]) {
      const name = config.prefix;
      await page.locator("#" + name + "-bulk-panel summary").click();
      const box = page.locator("#" + name + "-bulk-json");
      const consent = page.locator("#" + name + "-bulk-confirm");
      const stage = page.locator("#" + name + "-bulk-import");
      const status = page.locator("#" + name + "-bulk-status");
      await page.locator("#" + config.sample).click();
      const example = JSON.parse(await box.inputValue());
      expect(example).toHaveLength(2);
      await consent.check();
      // A whitespace-only human edit still invalidates earlier consent.
      await box.fill(JSON.stringify(example) + " ");
      await expect(consent).not.toBeChecked();
      await stage.click();
      await expect(status).toContainText("Confirm replacement");
      expect(await page.evaluate(editor => window[editor].delta(), config.editor)).toBeNull();
      expect((await tenant(request, headers)).revision).toBe(before.revision);
      await consent.check();
      await stage.click();
      await expect(status).toContainText("staged");
      await consent.check();
      await page.locator("#" + name + "-bulk-export").click();
      await expect(consent).not.toBeChecked();
      await consent.check();
      await page.locator("#" + config.sample).click();
      await expect(consent).not.toBeChecked();
      // Fill/export neither erase the approved staged draft nor change the tenant.
      expect(await page.evaluate(editor => window[editor].delta(), config.editor)).not.toBeNull();
      expect((await tenant(request, headers))[config.family]).toEqual(before[config.family]);
    }
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    expect((await tenant(request, headers)).revision).toBe(before.revision);
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const saved = await tenant(request, headers);
    expect(saved.revision).toBe(before.revision + 1);
    for (const family of ["comparisons", "advanced_arithmetic", "advanced_identity", "advanced_conversion_time"])
      expect(saved[family]).toHaveLength(2);
    const invalid = {
      invoice:{refund:75,paid:50},
      shipment:{delivery_date:"2026-11-01", order_date:"2026-11-02",
        order_id:"OTHER", load:50, load_unit:"kg", max_kg:20,
        serials:["A","A"],excursion:{start:"2026-01-01T10:00:00Z",
          end:"2026-01-01T11:00:00Z",allowed_minutes:30}},
      order:{id:"ORIGINAL"},
      billing:{subtotal:100,discount:0,tax:0,shipping:0,total:80},
      inventory:{opening:10,received:2,shipped:5,lost:1,closing:8}
    };
    const expected = [
      "business.refund_not_over_paid", "business.invoice_total_matches",
      "business.example_order_id_match", "business.example_cargo_kg_cap"
    ];
    for (const stage of ["input", "output"]) {
      const res = await request.post("/v1/checks/" + stage, {headers, data: {
        application_id:"demo2", content_format:"json", content:JSON.stringify(invalid)
      }});
      expect(res.status(), (await res.text()).slice(0, 200)).toBe(200);
      const data = await res.json();
      expect(data.evaluation_status).toBe("complete");
      for (const id of expected) expect(data.findings.map(f => f.rule_id)).toContain(id);
      expect(data.effective_action).toBe("block");
    }
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status(), (await reset.text()).slice(0, 200)).toBe(200);
    }
  }
});
