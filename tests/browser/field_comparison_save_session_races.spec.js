// Real browser + tenant API regression: an older tenant's delayed Save must
// never overwrite a newer customer's Field Comparisons UI or unsaved draft.
// The mutation is executed by the actual server before its response is held.
const {test, expect} = require("@playwright/test");

async function connect(page, tenant) {
  await page.locator("#demo-user").selectOption(tenant);
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to " + tenant);
  return await page.locator("#demo-key-value").inputValue();
}

async function holdActualSave(page) {
  let notify, resume;
  const arrived = new Promise(resolve => { notify = resolve; });
  const gate = new Promise(resolve => { resume = resolve; });
  await page.route("**/v1/tenants/current/checks", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const serverResponse = await route.fetch();
    notify(serverResponse.status()); // server applied/rejected the actual revisioned mutation
    await gate;
    await route.fulfill({response: serverResponse});
  });
  // The existing customer Save checks click handler still runs. Capture its
  // actual promise so browser assertions wait for completion, not a sleep.
  await page.evaluate(() => {
    const original = saveTenant;
    saveTenant = (...args) => {
      const pending = original(...args);
      window.__fcSaveResult = pending;
      return pending;
    };
  });
  return {arrived, release: () => resume()};
}

async function makeDraft(page) {
  const select = page.locator("#tenant-check-input-cross_field");
  await expect(select).toBeVisible();
  const original = await select.inputValue();
  const next = original === "block" ? "flag" : "block";
  await select.selectOption(next);
  await expect(page.locator("#tenant-message")).toContainText("Unsaved changes");
  return {select, original, next};
}

test("late FC Save from demo2 cannot replace demo4 tenant or newer draft", async ({page, request}) => {
  await page.goto("/dashboard");
  const demo2Key = await connect(page, "demo2");
  let held;
  try {
    const {next} = await makeDraft(page);
    held = await holdActualSave(page);
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    expect(await held.arrived, "real demo2 revisioned tenant write").toBe(200);

    await connect(page, "demo4");
    const demo4TenantId = await page.evaluate(() => application?.tenant?.tenant_id);
    const newer = await makeDraft(page);
    const newDraftValue = newer.next;
    held.release();

    expect(await page.evaluate(() => window.__fcSaveResult)).toBe(false);
    await expect(page.locator("#connection-status")).toContainText("Connected to demo4");
    await expect(newer.select).toHaveValue(newDraftValue);
    await expect(page.locator("#tenant-message")).toContainText("Unsaved changes");
    expect(await page.evaluate(() => application?.tenant?.tenant_id)).toBe(demo4TenantId);
    // The original mutation was actually issued, but its success message must
    // not appear on another tenant or imply that the demo4 draft was saved.
    await expect(page.locator("#tenant-message")).not.toContainText("Saved.");
    expect(["block", "flag"]).toContain(next);
  } finally {
    held?.release();
    const result = await request.post("/v1/tenants/current/reset", {
      headers: {"X-GuardOath-Key": demo2Key}, data: {}
    });
    expect(result.status(), "restore demo2 tenant").toBe(200);
  }
});

test("late FC Save does not erase a second unsaved edit in the same tenant", async ({page, request}) => {
  await page.goto("/dashboard");
  const demoKey = await connect(page, "demo2");
  let held;
  try {
    const draft = await makeDraft(page);
    held = await holdActualSave(page);
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    expect(await held.arrived, "real revisioned server write").toBe(200);
    // Save was accepted by the server, but this user then changed the
    // unsaved choice before its response reached the browser.
    await draft.select.selectOption(draft.original);
    held.release();
    expect(await page.evaluate(() => window.__fcSaveResult)).toBe(false);
    await expect(draft.select).toHaveValue(draft.original);
    await expect(page.locator("#tenant-message")).toContainText(
      "An earlier save may have changed this tenant while you edited.");
    await expect(page.locator("#connection-status")).toContainText("Connected to demo2");

    const response = await request.get("/v1/applications/current", {
      headers: {"X-GuardOath-Key": demoKey}
    });
    expect(response.status()).toBe(200);
    const actual = (await response.json()).tenant;
    expect(actual.input_checks.cross_field).toBe(draft.next);
  } finally {
    held?.release();
    const result = await request.post("/v1/tenants/current/reset", {
      headers: {"X-GuardOath-Key": demoKey}, data: {}
    });
    expect(result.status(), "restore demo2 tenant").toBe(200);
  }
});
