const { test, expect } = require("@playwright/test");

test("actual TSV attachment bytes use the existing authenticated tenant checks and block formulas", async ({ page }) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
  await expect(page.locator("#tenant-attachment-modes")).toContainText("CSV / TSV attachments");
  await page.locator("#tenant-check-input-csv_formula").selectOption("block");

  const file = Buffer.from("name\tvalue\nSynthetic\t=2+3", "utf8");
  // Deliberately untrusted filename and MIME: recognition comes only from bytes.
  await page.locator("#input-attachments").setInputFiles({
    name: "not-a-table.pdf", mimeType: "application/pdf", buffer: file,
  });
  const request = page.waitForResponse(r => r.url().endsWith("/v1/checks/input/attachments")
    && r.request().method() === "POST");
  await page.locator("#run-input").click();
  const response = await request;
  expect(response.request().postDataJSON().attachments_b64).toEqual([file.toString("base64")]);
  const outcome = await response.json();
  expect(outcome.attachments[0].kind).toBe("tsv");
  expect(outcome.attachments[0].reason).toBe("tsv_structure_recognized_only");
  expect(outcome.findings.map(f => f.rule_id)).toContain("csv_formula.potential_formula");
  expect(outcome.effective_action).toBe("block");
  await expect(page.locator("#input-results .attachment-receipts")).toContainText("tsv_structure_recognized_only");
  await expect(page.locator("#answer")).toBeHidden();
  await expect(page.locator("#input-results")).not.toContainText("Synthetic\t=2+3");
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
});

test("TSV output file picker independently blocks a formula after clean input", async ({ page }) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo7");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo7");
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
  await page.locator("#tenant-check-output-csv_formula").selectOption("block");

  const input = page.waitForResponse(r => r.url().endsWith("/v1/checks/input")
    && r.request().method() === "POST");
  await page.locator("#run-input").click();
  expect((await (await input).json()).effective_action).toBe("allow");

  await page.locator("#output-content").fill("Here is the spreadsheet summary.");
  await page.locator("#output-attachments").setInputFiles({
    name: "results.tsv", mimeType: "text/tab-separated-values",
    buffer: Buffer.from("label\tresult\nSynthetic\t@SUM(2,3)", "utf8"),
  });
  const request = page.waitForResponse(r => r.url().endsWith("/v1/checks/output/attachments")
    && r.request().method() === "POST");
  await page.locator("#run-output").click();
  const outcome = await (await request).json();
  expect(outcome.attachments[0].kind).toBe("tsv");
  expect(outcome.findings.map(f => f.rule_id)).toContain("csv_formula.potential_formula");
  expect(outcome.effective_action).toBe("block");
  await expect(page.locator("#output-results .attachment-receipts")).toContainText("tsv_structure_recognized_only");
  await expect(page.locator("#answer")).toBeHidden();
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
});
