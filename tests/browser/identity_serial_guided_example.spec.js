const {test, expect} = require("@playwright/test");

test("customer can configure and enforce serial uniqueness on both stages", async ({page}) => {
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
    expect(before.advanced_identity).toEqual([]);
    const references = before.advanced_identity_reference_status;

    await page.locator("#identity-sample-serial").click();
    await expect(page.locator("#identity-id")).toHaveValue("shipment_serials_unique");
    await expect(page.locator("#identity-kind")).toHaveValue("serial_unique");
    await expect(page.locator("#identity-field-serials")).toHaveValue("/shipment/serials");
    await expect(page.locator("#identity-stage-input")).toBeChecked();
    await expect(page.locator("#identity-stage-output")).toBeChecked();
    await expect(page.locator("#identity-message")).toContainText("not added or saved");
    expect((await current()).revision).toBe(before.revision);

    await page.locator("#identity-apply").click();
    await expect(page.locator("#identity-draft-summary")).toContainText("1 added");
    expect((await current()).advanced_identity).toEqual([]);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("block");
    await page.locator("#tenant-save").click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await current();
    expect(saved.revision).toBeGreaterThan(before.revision);
    expect(saved.advanced_identity).toContainEqual({
      rule_id: "shipment_serials_unique", kind: "serial_unique",
      fields: {serials: "/shipment/serials"}, stages: ["input", "output"], trusted_values: []
    });
    expect(saved.advanced_identity_reference_status).toEqual(references);

    for (const stage of ["input", "output"]) {
      for (const [serials, violates] of [
        [["SN100", "SN100"], true],
        [["SN100", "SN200"], false]
      ]) {
        const response = await page.request.post("/v1/checks/" + stage, {
          headers, data: {application_id: "demo8", content_format: "json",
                         content: JSON.stringify({shipment: {serials}})}
        });
        expect(response.status()).toBe(200);
        const body = await response.json();
        expect(body.evaluation_status).toBe("complete");
        expect(body.findings.some(f => f.rule_id === "business.shipment_serials_unique")).toBe(violates);
        if (violates) expect(body.effective_action).toBe("block");
      }
    }
    await page.locator("#identity-sample-serial").click();
    await expect(page.locator("#identity-message")).toContainText("already exists");
    expect((await current()).revision).toBe(saved.revision);
    expect((await current()).advanced_identity).toEqual(saved.advanced_identity);
  } finally {
    await reset();
  }
});
