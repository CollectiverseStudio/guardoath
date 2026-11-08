const { test, expect } = require("@playwright/test");

function rules(size, nested) {
  return Array.from({length: size}, (_, i) => {
    const n = String(i).padStart(3, "0");
    const prefix = nested ? "/" : "";
    return {
      rule_id: "shape_" + n,
      left: nested ? "/left/k_" + n : "/left_" + n,
      right: nested ? "/right/k_" + n : "/right_" + n,
      operator: "le",
      value_type: "number",
      stages: ["input", "output"]
    };
  });
}

test("field-comparison bulk editor warns on 501 flat rules, but not 501 nested rules", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo2");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo2");

  await page.locator("#comparison-bulk-panel > summary").click();
  const json = page.locator("#comparison-bulk-json");
  const confirm = page.locator("#comparison-bulk-confirm");
  const importButton = page.locator("#comparison-bulk-import");

  await json.fill(JSON.stringify(rules(501, false)));
  await confirm.check(); await importButton.click();
  await expect(page.locator("#comparison-draft-summary")).toContainText("501 comparisons configured");
  await expect(page.locator("#comparison-structure-warning")).toBeVisible();
  await expect(page.locator("#comparison-structure-warning")).toContainText("input references 1002 distinct top-level fields");
  await expect(page.locator("#comparison-structure-warning")).toContainText("output references 1002 distinct top-level fields");
  await expect(page.locator("#comparison-structure-warning")).toContainText("Use nested objects");

  await json.fill(JSON.stringify(rules(501, true)));
  await confirm.check(); await importButton.click();
  await expect(page.locator("#comparison-draft-summary")).toContainText("501 comparisons configured");
  await expect(page.locator("#comparison-structure-warning")).toBeHidden();
  await expect(page.locator("#comparison-draft-summary")).toContainText("Unsaved changes");
  // No Save checks action: draft import does not mutate authoritative tenant.
});
