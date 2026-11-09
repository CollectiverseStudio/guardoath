// FC-050 explicit optional return root, tested with the actual authenticated dashboard.
// No mocked API and no production tenant settings are changed.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo6");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo6");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

test("FC-050 explicit return-event boundary persists through the dashboard and protects incomplete returns", async ({page, request}) => {
  const headers = await connect(page);
  const read = async () => {
    const response = await request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const initial = await read();
    const cleared = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: initial.revision, changes: {
        comparisons: [], input_check_pii: "flag", output_check_pii: "flag"
      }
    }});
    expect(cleared.status(), (await cleared.text()).slice(0, 250)).toBe(200);
    await connect(page);

    await page.locator("#conversion-id").fill("ui_explicit_event");
    await page.locator("#conversion-kind").selectOption("lifecycle_chronology");
    const pointers = {
      order_at: "/order/at", shipment_at: "/shipment/at",
      delivery_at: "/delivery/at", return_at: "/returns/latest/at"
    };
    for (const [name, pointer] of Object.entries(pointers)) {
      await page.locator("#conversion-field-" + name).fill(pointer);
    }
    await expect(page.locator("#conversion-return-event-pointer")).toBeVisible();
    // A required delivery event cannot live in a supposedly optional root.
    const beforeInvalid = await read();
    await page.locator("#conversion-field-delivery_at").fill("/returns/delivered/at");
    await page.locator("#conversion-return-event-pointer").fill("/returns");
    await page.locator("#conversion-apply").click();
    await expect(page.locator("#conversion-message")).toContainText("cannot contain mandatory");
    await expect(page.locator('[data-conversion-id="ui_explicit_event"]')).toHaveCount(0);
    expect((await read()).revision).toBe(beforeInvalid.revision);
    await page.locator("#conversion-field-delivery_at").fill(pointers.delivery_at);
    await page.locator("#conversion-return-event-pointer").fill("/returns/latest");
    await page.locator("#conversion-apply").click();
    await expect(page.locator('[data-conversion-id="ui_explicit_event"]')).toHaveCount(1);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("flag");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await read();
    expect(saved.advanced_conversion_time).toHaveLength(1);
    expect(saved.advanced_conversion_time[0]).toMatchObject({
      rule_id: "ui_explicit_event", fields: pointers, stages: ["input", "output"],
      return_event_pointer: "/returns/latest"
    });
    await connect(page);
    await page.getByRole("button", {name: "Edit conversion ui_explicit_event"}).click();
    await expect(page.locator("#conversion-return-event-pointer")).toHaveValue("/returns/latest");
    await page.locator("#conversion-cancel").click();
    await page.locator("#rule-search").fill("ui_explicit_event");
    await expect(page.locator('tr.rule-row[data-rule="business.ui_explicit_event"]')).toBeVisible();
    await page.locator("#rule-search").fill("");

    const baseline = {
      order: {at: "2026-11-06T09:00:00Z"},
      shipment: {at: "2026-11-06T10:00:00Z"},
      delivery: {at: "2026-11-06T11:00:00Z"},
      returns: {}
    };
    const check = async (stage, document) => {
      const response = await request.post("/v1/checks/" + stage, {headers, data: {
        application_id: "demo6", content_format: "json", content: JSON.stringify(document)
      }});
      expect(response.status()).toBe(200);
      return response.json();
    };
    const publicRule = saved.record_rules.find(
      item => item.rule_id === "business.ui_explicit_event");
    expect(publicRule.no_return_example).toBeTruthy();
    const noReturnButton = page.getByRole("button", {
      name: "Try no-return input conversion ui_explicit_event"
    });
    await expect(noReturnButton).toBeVisible();
    await noReturnButton.click();
    await expect(page.locator("#input-format")).toHaveValue("json");
    const loaded = JSON.parse(await page.locator("#input-content").inputValue());
    expect(loaded).toEqual(JSON.parse(publicRule.no_return_example));
    const acceptedNoReturn = await check("input", loaded);
    expect(acceptedNoReturn.evaluation_status).toBe("complete");
    expect(acceptedNoReturn.findings.filter(f => f.rule_id.startsWith("business.")))
      .toEqual([]);
    const clean = await check("input", baseline);
    expect(clean.evaluation_status).toBe("complete");
    expect(clean.findings.filter(f => f.rule_id.startsWith("business."))).toEqual([]);
    for (const latest of [{}, {at: null}, {at: "2026-11-06T08:00:00Z"}]) {
      const bad = await check("output", {...baseline, returns: {latest}});
      expect(bad.evaluation_status).toBe("complete");
      expect(bad.findings.filter(f => f.rule_id.startsWith("business."))
        .map(f => f.rule_id)).toEqual(["business.ui_explicit_event"]);
      expect(bad.effective_action).toBe("flag");
      expect(JSON.stringify(bad.findings)).not.toContain("2026-11-06");
    }
    const invalid = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: saved.revision, changes: {
        advanced_conversion_time: [{
          ...saved.advanced_conversion_time[0], return_event_pointer: "/returns/other"
        }]
      }
    }});
    expect(invalid.status()).toBe(422);
    const unchanged = await read();
    expect(unchanged.revision).toBe(saved.revision);
    expect(unchanged.advanced_conversion_time[0].return_event_pointer).toBe("/returns/latest");
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
