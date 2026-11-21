"use strict";
const {test,expect} = require("@playwright/test");
const {installAdminFixture,openFixtureSession} = require("./admin_fixture");

test("owner usage shows source-bound historical request volume and no financial claim", async ({page}) => {
  const fixture = await installAdminFixture(page);
  await openFixtureSession(page);
  await expect(page.locator("#status")).toContainText("Administration loaded");
  await page.locator("#nav-tenants").click();
  await page.locator("#tenant-rows tr").filter({hasText:"sample-studio"})
    .getByRole("button",{name:"Usage",exact:true}).click();
  await expect(page.locator("#section-usage")).toBeVisible();
  const result = page.locator("#usage-details");
  await expect(result).toContainText("Historical recorded-request volume: within historical range");
  await expect(result).toContainText("Prior 3-month median: 2300");
  await expect(result).toContainText("Projected vs prior median: 95%");
  await expect(result).toContainText("historical records may be incomplete");
  await expect(result).toContainText("AWS costs: Unavailable");
  await expect(result).toContainText("Stripe invoices: Unavailable");
  expect(fixture.calls.filter(c=>c.path.includes("/usage")).every(c=>
    c.headers["x-guardoath-admin-session"]===fixture.token)).toBe(true);
  await page.locator("#logout").click();
  await expect(result).toBeEmpty();
});
