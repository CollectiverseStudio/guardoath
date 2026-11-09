// FC-050: real customer editor and Engine agree on an optional indexed return event.
// No mocked endpoints; all test records are synthetic and reset afterward.
const {test, expect} = require("@playwright/test");

const beforeReturn = {events: [
  {at: "2026-11-06T09:00:00Z"},
  {at: "2026-11-06T10:00:00Z"},
  {at: "2026-11-06T11:00:00Z"}
]};

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo4");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

test("FC-050 indexed optional return passes before return and blocks an invalid return on the saved dashboard", async ({page, request}) => {
  const headers = await connect(page);
  const read = async () => {
    const res = await request.get("/v1/applications/current", {headers});
    expect(res.status()).toBe(200);
    return (await res.json()).tenant;
  };
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;
    const baseline = await read();
    expect(baseline.comparisons.map(r => r.rule_id).sort()).toEqual(["date_order", "refund_limit"]);
    // Remove baseline cross-field comparisons to isolate the new rule's findings.
    const cleared = await request.post("/v1/tenants/current/checks", {headers, data: {
      revision: baseline.revision, changes: {comparisons: []}
    }});
    expect(cleared.status(), (await cleared.text()).slice(0, 200)).toBe(200);
    await connect(page);

    await page.locator("#conversion-id").fill("ui_optional_return");
    await page.locator("#conversion-kind").selectOption("lifecycle_chronology");
    for (const [role, pointer] of Object.entries({
      order_at: "/events/0/at",
      shipment_at: "/events/1/at",
      delivery_at: "/events/2/at",
      return_at: "/events/3/at"
    })) {
      await page.locator("#conversion-field-" + role).fill(pointer);
    }
    await page.locator("#conversion-apply").click();
    await expect(page.locator('[data-conversion-id="ui_optional_return"]')).toHaveCount(1);
    await page.locator("#tenant-check-input-cross_field").selectOption("block");
    await page.locator("#tenant-check-output-cross_field").selectOption("flag");
    const original = await read();
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved");
    const saved = await read();
    expect(saved.revision).toBe(original.revision + 1);
    expect(saved.advanced_conversion_time).toHaveLength(1);
    expect(saved.advanced_conversion_time[0]).toMatchObject({
      rule_id: "ui_optional_return",
      fields: {
        order_at: "/events/0/at", shipment_at: "/events/1/at",
        delivery_at: "/events/2/at", return_at: "/events/3/at"
      },
      stages: ["input", "output"]
    });

    await connect(page);
    await expect(page.locator('[data-conversion-id="ui_optional_return"]')).toHaveCount(1);
    await page.locator("#rule-search").fill("ui_optional_return");
    await expect(page.locator('tr.rule-row[data-rule="business.ui_optional_return"]')).toBeVisible();
    await page.locator("#rule-search").fill("");

    await page.locator("#input-format").selectOption("json");
    await page.locator("#input-content").fill(JSON.stringify(beforeReturn));
    const inputRequest = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
    await page.locator("#run-input").click();
    const input = await (await inputRequest).json();
    expect(input.evaluation_status).toBe("complete");
    expect(input.effective_action).toBe("allow");
    expect(input.findings.filter(f => f.rule_id === "business.ui_optional_return")).toEqual([]);
    await expect(page.locator("#run-output")).toBeEnabled();

    // An actual fourth return event must be ordered; the optional rule does not
    // mean a supplied invalid timestamp is ignored.
    await page.locator("#output-format").selectOption("json");
    await page.locator("#output-content").fill(JSON.stringify({
      events: [...beforeReturn.events, {at: "2026-11-06T08:00:00Z"}]
    }));
    const outputRequest = page.waitForResponse(r =>
      r.url().endsWith("/v1/checks/output") && r.request().method() === "POST");
    await page.locator("#run-output").click();
    const output = await (await outputRequest).json();
    expect(output.evaluation_status).toBe("complete");
    expect(output.findings.filter(f => f.rule_id === "business.ui_optional_return")).toHaveLength(1);
    expect(output.effective_action).toBe("flag");
    await expect(page.locator('#output-results button[aria-label="Review output rule settings for business.ui_optional_return"]')).toHaveCount(1);

    // Missing required delivery event is not an optional return.
    const incomplete = await request.post("/v1/checks/input", {headers, data: {
      application_id: "demo4", content_format: "json",
      content: JSON.stringify({events: beforeReturn.events.slice(0, 2)})
    }});
    expect(incomplete.status()).toBe(200);
    const result = await incomplete.json();
    expect(result.evaluation_status).toBe("complete");
    expect(result.effective_action).toBe("block");
    expect(result.findings.map(f => f.rule_id)).toContain("business.ui_optional_return");
  } finally {
    if (resetRequired) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(reset.status()).toBe(200);
    }
  }
});
