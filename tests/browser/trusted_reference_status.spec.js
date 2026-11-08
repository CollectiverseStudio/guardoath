const {test, expect} = require("@playwright/test");

test("host-attested tenant reference panel exposes only safe status and navigates stage actions", async ({page}) => {
  await page.route("**/v1/applications/current", async route => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.tenant) {
      body.tenant.advanced_trusted_reference_status = [{
        rule_id: "synthetic_signed_rule", kind: "permit_cap",
        stages: ["input", "output"], snapshot_present: true,
        runtime_integrity: "blocked"
      }];
    }
    await route.fulfill({response, json:body});
  });
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo7");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo7");
  const panel = page.locator("#trusted-reference-status");
  await expect(panel).toBeVisible();
  await expect(panel).toContainText("synthetic_signed_rule");
  await expect(panel).toContainText("permit_cap");
  await expect(panel).toContainText("saved snapshot: present");
  await expect(panel).toContainText("runtime integrity: blocked");
  await expect(panel).toContainText("active validation will Block");
  await expect(panel).toContainText("not proof of current signature");
  await expect(panel).not.toContainText("host_signature");
  await expect(panel).not.toContainText("reference_values");
  await expect(panel.locator("input")).toHaveCount(0);
  const button = panel.getByRole("button", {
    name:"Review output action for host reference synthetic_signed_rule"
  });
  await button.click();
  await expect(page.locator("#tenant-message")).toContainText("Review the output");
  await expect(page.locator("#tenant-check-output-cross_field")).toBeFocused();
  await expect(page.locator("#tenant-message")).toContainText("no settings were changed");
});

test("host-attested reference panel has explicit empty inventory, not a fabricated verification claim", async ({page}) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo7");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo7");
  await expect(page.locator("#trusted-reference-status")).toContainText(
    "No host-attested Field Comparison snapshots on this tenant.");
});

test("host-reference status shows current accepted integrity without claiming external source truth", async ({page}) => {
  await page.route("**/v1/applications/current", async route => {
    const response = await route.fetch();
    const body = await response.json();
    if (body.tenant) {
      body.tenant.advanced_trusted_reference_status = [{
        rule_id: "synthetic_current", kind: "invoice_po_vendor",
        stages: ["input"], snapshot_present: true, runtime_integrity: "accepted"
      }];
    }
    await route.fulfill({response, json: body});
  });
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo7");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo7");
  const panel = page.locator("#trusted-reference-status");
  await expect(panel).toContainText("runtime integrity: accepted");
  await expect(panel).toContainText("external truth not verified");
  await expect(panel).not.toContainText("host_signature");
  await expect(panel.locator("input")).toHaveCount(0);
});
