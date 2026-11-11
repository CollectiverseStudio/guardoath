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


test("dashboard sample button loads actual TSV bytes and runs a real authenticated input check", async ({ page }) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
  await expect(page.locator("#input-attachments")).toHaveAttribute("accept", /\.tsv/);
  await page.locator("#tenant-check-input-csv_formula").selectOption("block");

  await page.locator("#input-tsv-sample").click();
  await expect(page.locator("#input-attachment-note")).toContainText("Sample TSV loaded");
  const selected = await page.locator("#input-attachments").evaluate(async element => ({
    name: element.files[0]?.name,
    body: element.files[0] ? await element.files[0].text() : null,
  }));
  expect(selected).toEqual({name: "synthetic-formula.tsv", body: "name\\tvalue\\nSynthetic\\t=2+3".replaceAll("\\t", "\t").replace("\\n", "\n")});
  const response = page.waitForResponse(r => r.url().endsWith("/v1/checks/input/attachments") && r.request().method() === "POST");
  await page.locator("#run-input").click();
  const checked = await response;
  const outcome = await checked.json();
  expect(outcome.attachments[0].kind).toBe("tsv");
  expect(outcome.findings.map(f => f.rule_id)).toContain("csv_formula.potential_formula");
  expect(outcome.effective_action).toBe("block");
  await expect(page.locator("#answer")).toBeHidden();

  await page.locator("#input-attachment-clear").click();
  expect(await page.locator("#input-attachments").evaluate(e => e.files.length)).toBe(0);
  await expect(page.locator("#input-attachment-note")).toContainText("No file selected");
  await expect(page.locator("#input-results")).not.toContainText("tsv_structure_recognized_only");
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
});

test("dashboard output TSV sample requires passed input and clears independently", async ({ page }) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo7");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo7");
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
  await page.locator("#output-tsv-sample").click();
  await expect(page.locator("#output-attachment-note")).toContainText("Pass input checks");
  expect(await page.locator("#output-attachments").evaluate(e => e.files.length)).toBe(0);

  await page.locator("#tenant-check-output-csv_formula").selectOption("block");
  const input = page.waitForResponse(r => r.url().endsWith("/v1/checks/input") && r.request().method() === "POST");
  await page.locator("#run-input").click();
  expect((await (await input).json()).effective_action).toBe("allow");

  await page.locator("#output-tsv-sample").click();
  await expect(page.locator("#output-attachment-note")).toContainText("Sample TSV loaded");
  const output = page.waitForResponse(r => r.url().endsWith("/v1/checks/output/attachments") && r.request().method() === "POST");
  await page.locator("#run-output").click();
  const result = await (await output).json();
  expect(result.attachments[0].kind).toBe("tsv");
  expect(result.findings.map(f => f.rule_id)).toContain("csv_formula.potential_formula");
  expect(result.effective_action).toBe("block");

  await page.locator("#output-attachment-clear").click();
  expect(await page.locator("#output-attachments").evaluate(e => e.files.length)).toBe(0);
  await expect(page.locator("#output-attachment-note")).toContainText("No file selected");
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
});


test("per-file checker provenance is required before an attachment result can be shown or passed", async ({ page }) => {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", { name: "Get demo API key", exact: true }).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");

  const reset = page.waitForResponse(r => r.url().endsWith("/v1/tenants/current/reset")
    && r.request().method() === "POST");
  await page.getByRole("button", { name: "Reset to defaults", exact: true }).click();
  expect((await reset).status()).toBe(200);
  await expect(page.locator("#tenant-attachment-modes")).toContainText("CSV / TSV attachments");
  await page.locator("#tenant-check-input-csv_formula").selectOption("block");

  const file = Buffer.from("name\tvalue\nSynthetic\t=2+3", "utf8");
  await page.locator("#input-attachments").setInputFiles({
    name: "untrusted.tsv", mimeType: "text/tab-separated-values", buffer: file,
  });

  // Fetch the real shared Engine response, then strip ONLY its scoped evidence
  // marker. A 200 response and plausible findings are not sufficient to
  // authorize a result whose per-file checker provenance is ambiguous.
  const url = "**/v1/checks/input/attachments";
  const corrupt = async route => {
    const response = await route.fetch();
    expect(response.status()).toBe(200);
    const payload = await response.json();
    const check = payload.checker_results.find(c => c.attachment_index === 0);
    expect(check).toBeDefined();
    delete check.attachment_index;
    await route.fulfill({response, json: payload});
  };
  await page.route(url, corrupt);
  await page.locator("#run-input").click();
  await expect(page.locator("#result-status")).toHaveText("Check failed");
  await expect(page.locator("#run-error")).toContainText("receipt was invalid");
  await expect(page.locator("#input-results .attachment-receipts")).toHaveCount(0);
  await expect(page.locator("#run-output")).toBeDisabled();

  // The real, correctly scoped server response must still display the actual
  // inspection receipt and the blocking finding without bypassing any checks.
  await page.unroute(url, corrupt);
  await page.locator("#run-input").click();
  await expect(page.locator("#input-results .attachment-receipts"))
    .toContainText("tsv_structure_recognized_only");
  await expect(page.locator("#input-results")).toContainText("Recommended: block");
  await expect(page.locator("#answer")).toBeHidden();
});
