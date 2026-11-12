const {test, expect} = require("@playwright/test");

test("customer configures cold-chain elapsed-time rule on Input and Output", async ({page}) => {
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
    const references = before.advanced_conversion_reference_status;

    await page.locator("#conversion-sample-cold-chain").click();
    await expect(page.locator("#conversion-id")).toHaveValue("cold_chain_duration_within_limit");
    await expect(page.locator("#conversion-kind")).toHaveValue("cold_chain_excursion");
    for (const [field, pointer] of [
      ["excursion_start", "/shipment/excursion/start"],
      ["excursion_end", "/shipment/excursion/end"],
      ["allowed_minutes", "/shipment/excursion/allowed_minutes"]
    ]) await expect(page.locator("#conversion-field-" + field)).toHaveValue(pointer);
    await expect(page.locator("#conversion-stage-input")).toBeChecked();
    await expect(page.locator("#conversion-stage-output")).toBeChecked();
    await expect(page.locator("#conversion-message")).toContainText("not added or saved");
    expect((await current()).revision).toBe(before.revision);
    await page.locator("#conversion-apply").click();
    await expect(page.locator("#conversion-draft-summary")).toContainText("1 added");
    expect((await current()).advanced_conversion_time).toEqual([]);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await current();
    expect(saved.revision).toBeGreaterThan(before.revision);
    expect(saved.advanced_conversion_time).toContainEqual(expect.objectContaining({
      rule_id: "cold_chain_duration_within_limit", kind: "cold_chain_excursion",
      fields: {excursion_start: "/shipment/excursion/start",
               excursion_end: "/shipment/excursion/end",
               allowed_minutes: "/shipment/excursion/allowed_minutes"},
      stages: ["input", "output"]
    }));
    expect(saved.advanced_conversion_reference_status).toEqual(references);
    for (const stage of ["input", "output"]) {
      for (const [end, violates] of [
        ["2026-01-01T10:45:00Z", true],
        ["2026-01-01T10:20:00Z", false]
      ]) {
        const response = await page.request.post("/v1/checks/" + stage, {
          headers, data: {application_id: "demo8", content_format: "json",
            content: JSON.stringify({shipment: {excursion: {
              start: "2026-01-01T10:00:00Z", end, allowed_minutes: 30
            }}})}
        });
        expect(response.status()).toBe(200);
        const body = await response.json();
        expect(body.evaluation_status).toBe("complete");
        expect(body.findings.some(f => f.rule_id === "business.cold_chain_duration_within_limit")).toBe(violates);
        if (violates) expect(body.effective_action).toBe("block");
      }
    }
    await page.locator("#conversion-sample-cold-chain").click();
    await expect(page.locator("#conversion-message")).toContainText("already exists");
    expect((await current()).revision).toBe(saved.revision);
  } finally {
    await reset();
  }
});
