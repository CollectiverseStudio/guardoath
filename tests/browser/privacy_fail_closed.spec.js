// Real tenant/API/dashboard privacy fail-closed acceptance (issue #30).
// No mock API, PII recognizer, enlarged deadline, disabled check or paid provider.
const {test, expect} = require("@playwright/test");

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo8");
  await page.getByRole("button", {name:"Get demo API key", exact:true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo8");
  return {Authorization:"Bearer "+await page.locator("#demo-key-value").inputValue()};
}
async function tenant(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status()).toBe(200);
  return (await response.json()).tenant;
}
test("real 65-field privacy capacity limit blocks both stages even in observe/Flag, dashboard holds answer", async ({page, request}) => {
  const headers = await connect(page);
  let mustReset = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data:{}});
    expect(reset.status()).toBe(200);
    mustReset = true;
    const before = await tenant(request,headers);
    const saved = await request.post("/v1/tenants/current/checks", {headers, data:{
      revision: before.revision,
      changes: {
        mode:"observe", failure_action:"flag",
        input_check_pii:"flag", output_check_pii:"flag",
        detect_labeled_sensitive_data:true,
      },
    }});
    expect(saved.status(), (await saved.text()).slice(0,160)).toBe(200);
    const live = await tenant(request,headers);
    expect(live.revision).toBe(before.revision+1);
    expect(live.mode).toBe("observe");
    expect(live.failure_action).toBe("flag");
    expect(live.input_check_pii).toBe("flag");
    expect(live.output_check_pii).toBe("flag");
    expect(live.detect_labeled_sensitive_data).toBe(true);

    const content = JSON.stringify({numbers: Array.from({length:65}, ()=>({phone_number:"2025550199"}))});
    for (const stage of ["input","output"]) {
      const response = await request.post("/v1/checks/"+stage, {headers, data:{
        application_id:"demo8", content_format:"json", content,
      }});
      expect(response.status()).toBe(200);
      const result = await response.json();
      const pii = result.checker_results.find(c=>c.checker_id==="pii");
      expect(pii).toMatchObject({checker_id:"pii",status:"error",error_code:"pii_field_limit"});
      expect(result.evaluation_status).toBe("incomplete");
      expect(result.recommended_action).toBe("block");
      expect(result.effective_action).toBe("block");
      expect(result.redaction?.content ?? null).toBeNull();
      expect(JSON.stringify(result)).not.toContain("2025550199");
    }

    await connect(page);
    await page.locator("#input-format").selectOption("json");
    await page.locator("#input-content").fill(content);
    await page.locator("#run-input").click();
    await expect(page.locator("#input-results")).toContainText("pii_field_limit");
    await expect(page.locator("#input-results")).toContainText("Effective: block");
    await expect(page.locator("#result-status")).toHaveText("Incomplete");
    await expect(page.locator("#run-output")).toBeDisabled();
    await expect(page.locator("#answer")).toBeHidden();
  } finally {
    if (mustReset) {
      const reset = await request.post("/v1/tenants/current/reset", {headers, data:{}});
      expect(reset.status()).toBe(200);
    }
  }
});
