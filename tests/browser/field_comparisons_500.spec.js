// Dashboard-only 500-rule rendering acceptance. The real tenant/API Engine scale
// acceptance lives in tests/test_field_comparison_large_tenant_api.py.
// This test replaces only the browser's GET response with synthetic tenant
// metadata; it does not create or modify a real tenant or claim backend scale.
const { test, expect } = require("@playwright/test");

test("500 tenant comparisons remain searchable and independently configurable in the dashboard", async ({ page }) => {
  const count = 500;
  await page.route("**/v1/applications/current", async route => {
    const response = await route.fetch();
    const data = await response.json();
    if (!data.tenant) return route.fulfill({ response });
    const originals = (data.tenant.record_rules || []).filter(
      rule => rule.control_id !== "cross_field"
    );
    const synthetic = Array.from({ length: count }, (_, i) => {
      const n = String(i + 1).padStart(3, "0");
      return {
        rule_id: "business.ui_scale_" + n,
        control_id: "cross_field",
        title: "Scale comparison " + n,
        use_case: "Synthetic browser-scale rendering of tenant comparison " + n,
        configuration: "left /left_" + n + " <= right /right_" + n,
        severity: "medium",
        stages: ["input", "output"],
        content_format: "json",
        record_rule: true,
        example: '{"left_' + n + '":2,"right_' + n + '":1}',
        look_alike: '{"left_' + n + '":1,"right_' + n + '":2}'
      };
    });
    data.tenant.record_rules = originals.concat(synthetic);
    await route.fulfill({ response, json: data });
  });

  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");

  const large = page.locator('#tenant-group-cross_field .rule-row[data-rule^="business.ui_scale_"]');
  await expect(large).toHaveCount(count);
  await expect(page.locator("#tenant-rules-cross_field")).toContainText("501");
  await page.locator("#rule-search").fill("ui_scale_500");
  const last = page.locator('[data-rule="business.ui_scale_500"]');
  await expect(last).toBeVisible();
  await expect(last.locator(".rule-title")).toHaveText("Scale comparison 500");
  await expect(page.locator('[data-rule="business.ui_scale_001"]')).toBeHidden();

  await page.locator("#tenant-check-input-cross_field").selectOption("flag");
  await page.locator("#tenant-check-output-cross_field").selectOption("block");
  await expect(last.locator(".effect-input")).toHaveText("Input: Flag (from check)");
  await expect(last.locator(".effect-output")).toHaveText("Output: Block (from check)");
  await last.locator('[data-stage="input"]').selectOption("block");
  await expect(last.locator(".effect-input")).toHaveText("Input: Block (own setting)");
  await expect(last.locator(".effect-output")).toHaveText("Output: Block (from check)");

  await page.locator("#rule-search").fill("ui_scale_001");
  const first = page.locator('[data-rule="business.ui_scale_001"]');
  await expect(first).toBeVisible();
  await expect(first.locator(".effect-input")).toHaveText("Input: Flag (from check)");
  await expect(first.locator(".effect-output")).toHaveText("Output: Block (from check)");
  // Do not save the synthetic rules; backend persistence and Engine evaluation
  // are independently verified at the five required scales.
});
