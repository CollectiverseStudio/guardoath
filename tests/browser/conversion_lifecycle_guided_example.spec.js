const {test, expect} = require("@playwright/test");

test("customer saves and enforces lifecycle chronology including optional return provenance on both stages", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  const headers = {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
  const current = async () => {
    const response = await page.request.get("/v1/applications/current", {headers});
    expect(response.status()).toBe(200);
    return (await response.json()).tenant;
  };
  const reset = async () => {
    const response = await page.request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(response.status()).toBe(200);
  };
  await reset();
  try {
    await page.reload();
    await page.locator("#comparison-configuration").evaluate(el => { el.open = true; });
    await page.locator("#demo-user").selectOption("demo8");
    await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
    await expect(page.locator("#connection-status")).toContainText("Connected to demo8");

    const before = await current();
    expect(before.advanced_conversion_time).toEqual([]);
    const protectedReferences = before.advanced_conversion_reference_status;
    await page.locator("#conversion-sample-lifecycle").click();
    await expect(page.locator("#conversion-id")).toHaveValue("fulfillment_lifecycle_ordered");
    await expect(page.locator("#conversion-kind")).toHaveValue("lifecycle_chronology");
    for (const [field, pointer] of [
      ["order_at", "/fulfillment/order_at"],
      ["shipment_at", "/fulfillment/shipment_at"],
      ["delivery_at", "/fulfillment/delivery_at"],
      ["return_at", "/fulfillment/return/at"]
    ]) await expect(page.locator("#conversion-field-" + field)).toHaveValue(pointer);
    await expect(page.locator("#conversion-return-event-pointer")).toHaveValue("/fulfillment/return");
    await expect(page.locator("#conversion-stage-input")).toBeChecked();
    await expect(page.locator("#conversion-stage-output")).toBeChecked();
    await expect(page.locator("#conversion-message")).toContainText("not added or saved");
    expect((await current()).revision).toBe(before.revision);

    await page.locator("#conversion-apply").click();
    await expect(page.locator("#conversion-draft-summary")).toContainText("1 added");
    expect((await current()).advanced_conversion_time).toEqual(before.advanced_conversion_time);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await current();
    expect(saved.revision).toBeGreaterThan(before.revision);
    expect(saved.advanced_conversion_time).toContainEqual(expect.objectContaining({
      rule_id: "fulfillment_lifecycle_ordered",
      kind: "lifecycle_chronology",
      fields: {
        order_at: "/fulfillment/order_at",
        shipment_at: "/fulfillment/shipment_at",
        delivery_at: "/fulfillment/delivery_at",
        return_at: "/fulfillment/return/at"
      },
      return_event_pointer: "/fulfillment/return",
      stages: ["input", "output"]
    }));
    expect(saved.advanced_conversion_reference_status).toEqual(protectedReferences);

    const common = {order_at: "2026-01-01T09:00:00Z", shipment_at: "2026-01-01T10:00:00Z",
                    delivery_at: "2026-01-01T11:00:00Z"};
    const examples = [
      [{...common, shipment_at: "2026-01-01T12:00:00Z"}, true],
      [common, false], // no return has yet occurred
      [{...common, return: {at: "2026-01-01T13:00:00Z"}}, false],
      [{...common, return: {}}, true], // recorded but incomplete return must fail
      [{...common, return: {at: "2026-01-01T08:00:00Z"}}, true]
    ];
    for (const stage of ["input", "output"]) {
      for (const [fulfillment, violates] of examples) {
        const response = await page.request.post("/v1/checks/" + stage, {
          headers, data: {
            application_id: "demo8", content_format: "json",
            content: JSON.stringify({fulfillment})
          }
        });
        expect(response.status()).toBe(200);
        const body = await response.json();
        expect(body.evaluation_status).toBe("complete");
        expect(body.findings.some(f => f.rule_id === "business.fulfillment_lifecycle_ordered")).toBe(violates);
        if (violates) expect(body.effective_action).toBe("block");
      }
    }
    await page.locator("#conversion-sample-lifecycle").click();
    await expect(page.locator("#conversion-message")).toContainText("already exists");
    expect((await current()).revision).toBe(saved.revision);
    expect((await current()).advanced_conversion_time).toEqual(saved.advanced_conversion_time);
  } finally {
    await reset();
  }
});
