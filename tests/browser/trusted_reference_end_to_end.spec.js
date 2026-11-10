// Real authenticated browser + tenant API acceptance for host-signed FC-047.
// Unlike the panel-only presentation tests, no application response is mocked.
// The key is a synthetic test-only fixture set only on Playwright's local server.
const {test, expect} = require("@playwright/test");
const {execFileSync} = require("node:child_process");

const KEY = "synthetic-playwright-host-signing-key-not-for-production-0123456789";
const SIGNER = [
  "import json, sys",
  "from guardoath.models import AdvancedTrustedReferenceRule",
  "from guardoath.trusted_reference import sign_reference",
  "args = json.load(sys.stdin)",
  "rule = AdvancedTrustedReferenceRule.model_validate(args['rule'])",
  "signed = rule.model_copy(update={'host_signature': sign_reference(rule, args['tenant'], args['key'])})",
  "print(json.dumps(signed.model_dump(mode='json')))",
].join("\n");

function signedReference() {
  const now = Date.now();
  const stamp = ms => new Date(ms).toISOString().replace(/\.000Z$/, "Z");
  const rule = {
    rule_id: "real_browser_permit", kind: "permit_cap",
    fields: {reported_emission: "/reported_emission"},
    reference_values: {permit_limit: "50"},
    source_id: "synthetic-verified-source", reference_version: "synthetic-v1",
    verified_at: stamp(now - 60_000), expires_at: stamp(now + 86_400_000),
    host_signature: "0".repeat(64), stages: ["input", "output"],
  };
  return JSON.parse(execFileSync("python", ["-c", SIGNER], {
    input: JSON.stringify({rule, tenant: "demo7", key: KEY}),
    encoding: "utf8", timeout: 5000,
  }));
}

async function connect(page) {
  await page.goto("/dashboard");
  await page.locator("#demo-user").selectOption("demo7");
  await page.getByRole("button", {name: "Get demo API key", exact: true}).click();
  await expect(page.locator("#connection-status")).toContainText("Connected to demo7");
  return {Authorization: "Bearer " + await page.locator("#demo-key-value").inputValue()};
}

async function summary(request, headers) {
  const response = await request.get("/v1/applications/current", {headers});
  expect(response.status()).toBe(200);
  return (await response.json()).tenant;
}

async function evaluate(request, headers, stage, emission) {
  const response = await request.post("/v1/checks/" + stage, {
    headers, data: {
      application_id: "demo7", content_format: "json",
      content: JSON.stringify({reported_emission: emission}),
    },
  });
  expect(response.status()).toBe(200);
  return response.json();
}

test("real signed FC-047 reference moves accepted to blocked after tampering, with customer-visible Input/Output effects", async ({page, request}) => {
  const headers = await connect(page);
  let resetRequired = false;
  try {
    const reset = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(reset.status()).toBe(200);
    resetRequired = true;

    const signed = signedReference();
    const prior = await summary(request, headers);
    const saved = await request.post("/v1/tenants/current/checks", {
      headers, data: {
        revision: prior.revision,
        changes: {
          // Reset restores two ordinary demo comparison templates whose
          // required fields are absent from this synthetic permit document.
          // Make the tenant portfolio exactly FC-047 before asserting clean
          // results; leave every independent PII/Secrets check enabled.
          comparisons: [],
          advanced_trusted_reference: [signed],
          input_check_cross_field: "block",
          output_check_cross_field: "flag",
        },
      },
    });
    expect(saved.status(), (await saved.text()).slice(0, 150)).toBe(200);

    const live = await summary(request, headers);
    expect(live.revision).toBe(prior.revision + 1);
    expect(live.comparisons).toEqual([]);
    const status = live.advanced_trusted_reference_status;
    expect(status).toEqual([{
      rule_id: "real_browser_permit", kind: "permit_cap",
      stages: ["input", "output"], snapshot_present: true,
      runtime_integrity: "accepted",
    }]);
    const statusText = JSON.stringify(status);
    for (const privateValue of [KEY, signed.host_signature, signed.source_id,
      signed.verified_at, signed.expires_at, signed.reference_values.permit_limit]) {
      expect(statusText).not.toContain(privateValue);
    }

    await connect(page);
    const panel = page.locator("#trusted-reference-status");
    await expect(panel).toContainText("real_browser_permit");
    await expect(panel).toContainText("runtime integrity: accepted");
    await expect(panel).toContainText("external truth not verified");
    await expect(panel.locator("input")).toHaveCount(0);
    await panel.getByRole("button", {
      name: "Review output action for host reference real_browser_permit",
    }).click();
    await expect(page.locator('tr.rule-row[data-rule="business.real_browser_permit"] [data-stage="output"]')).toBeFocused();
    await expect(page.locator("#tenant-message")).toContainText("Review output rule business.real_browser_permit");
    await expect(page.locator("#tenant-message")).toContainText("No settings were changed");

    for (const [stage, action] of [["input", "block"], ["output", "flag"]]) {
      const failed = await evaluate(request, headers, stage, 60);
      expect(failed.evaluation_status).toBe("complete");
      expect(failed.effective_action).toBe(action);
      expect(failed.findings.map(f => f.rule_id)).toContain("business.real_browser_permit");
      const clean = await evaluate(request, headers, stage, 40);
      expect(clean.evaluation_status).toBe("complete");
      expect(clean.effective_action).toBe("allow");
      expect(clean.findings.some(f => f.rule_id === "business.real_browser_permit")).toBe(false);
    }

    // A saved syntactically valid snapshot is NOT authority without a matching
    // host signature. The server recomputes integrity, rather than trusting
    // the dashboard or a client-provided accepted flag.
    const tampered = {...signed, reference_values: {permit_limit: "500"}};
    const changed = await request.post("/v1/tenants/current/checks", {
      headers, data: {
        revision: live.revision,
        changes: {advanced_trusted_reference: [tampered]},
      },
    });
    expect(changed.status(), (await changed.text()).slice(0, 150)).toBe(200);
    const blocked = await summary(request, headers);
    expect(blocked.revision).toBe(live.revision + 1);
    expect(blocked.advanced_trusted_reference_status[0].runtime_integrity).toBe("blocked");
    await connect(page);
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("runtime integrity: blocked");
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("active validation will Block");
    for (const stage of ["input", "output"]) {
      const failedClosed = await evaluate(request, headers, stage, 40);
      expect(failedClosed.evaluation_status).toBe("incomplete");
      expect(failedClosed.effective_action).toBe("block");
      expect(failedClosed.findings.map(f => f.rule_id))
        .toContain("business.real_browser_permit");
    }

    // Customer UI must not imply an invalid host reference Blocks a stage
    // after that individual rule or its parent cross_field check is saved Off.
    // Keep the tampered reference invalid: no re-signing or mocked Engine.
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("Input: active (check block; rule inherits check)");
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("Output: active (check flag; rule inherits check)");
    const outRow = page.locator(
      'tr.rule-row[data-rule="business.real_browser_permit"] [data-stage="output"]');
    await outRow.selectOption("off");
    // Pending UI state must not impersonate the persisted tenant record.
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("Output: active (check flag; rule inherits check)");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const disabledRule = await summary(request, headers);
    expect(disabledRule.revision).toBe(blocked.revision + 1);
    expect(disabledRule.output_rule_actions["business.real_browser_permit"]).toBe("off");
    expect(disabledRule.advanced_trusted_reference_status[0].runtime_integrity).toBe("blocked");
    await connect(page);
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("Output: Off (individual rule Off)");
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("Input: active (check block; rule inherits check)");
    const safeOutput = await evaluate(request, headers, "output", 40);
    expect(safeOutput.evaluation_status).toBe("complete");
    expect(safeOutput.findings.some(f => f.rule_id === "business.real_browser_permit")).toBe(false);
    expect((await evaluate(request, headers, "input", 40)).effective_action).toBe("block");

    await page.locator("#tenant-check-input-cross_field").selectOption("");
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("Input: active (check block; rule inherits check)");
    await page.getByRole("button", {name: "Save checks", exact: true}).click();
    await expect(page.locator("#tenant-message")).toContainText("Saved.");
    const disabledParent = await summary(request, headers);
    expect(disabledParent.revision).toBe(disabledRule.revision + 1);
    expect(disabledParent.input_checks.cross_field).toBeNull();
    expect(disabledParent.advanced_trusted_reference_status[0].runtime_integrity).toBe("blocked");
    await connect(page);
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("Input: Off (Field comparisons check Off)");
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("Output: Off (individual rule Off)");
    await expect(page.locator("#trusted-reference-status"))
      .toContainText("blocked (all saved stages Off; enabling one will Block until re-attested)");
    for (const stage of ["input", "output"]) {
      const disabled = await evaluate(request, headers, stage, 40);
      expect(disabled.evaluation_status).toBe("complete");
      expect(disabled.findings.some(f => f.rule_id === "business.real_browser_permit")).toBe(false);
    }
    const publicStatus = await page.locator("#trusted-reference-status").innerText();
    for (const secret of [KEY, signed.host_signature, signed.source_id, signed.reference_values.permit_limit]) {
      expect(publicStatus).not.toContain(secret);
    }
  } finally {
    if (resetRequired) {
      const cleanup = await request.post("/v1/tenants/current/reset", {headers, data: {}});
      expect(cleanup.status()).toBe(200);
    }
  }
});

test("FC-044/045/049 coexist: signed tenant status, dashboard and both Engine stages", async ({page, request}) => {
  const headers = await connect(page);
  let reset = false;
  try {
    const cleared = await request.post("/v1/tenants/current/reset", {headers, data: {}});
    expect(cleared.status()).toBe(200);
    reset = true;
    const now = Date.now();
    const stamp = ms => new Date(ms).toISOString();
    const definitions = [
      ["dose", "dose_ceiling", {dose_mg:"/dose_mg"}, {verified_weight_kg:"70",max_mg_per_kg:"10"}, {dose_mg:900}],
      ["interval", "medication_interval", {administered_at:"/administered_at"}, {previous_administration:"2026-11-01T09:00:00Z",minimum_interval_minutes:"240"}, {administered_at:"2026-11-01T10:00:00Z"}],
      ["vendor", "invoice_po_vendor", {invoice_vendor_id:"/invoice_vendor_id"}, {approved_vendor_id:"VENDOR1"}, {invoice_vendor_id:"VENDOR2"}],
    ];
    const signed = definitions.map(([name,kind,fields,values]) => {
      const rule = {rule_id:"browser_"+name,kind,fields,reference_values:values,
        source_id:"synthetic-fixture",reference_version:"v1",
        verified_at:stamp(now-60000),expires_at:stamp(now+86400000),
        host_signature:"0".repeat(64),stages:["input","output"]};
      return JSON.parse(execFileSync("python",["-c",SIGNER],{
        input:JSON.stringify({rule,tenant:"demo7",key:KEY}),encoding:"utf8",timeout:5000,
      }));
    });
    const prior = await summary(request,headers);
    const saved = await request.post("/v1/tenants/current/checks",{headers,data:{
      revision:prior.revision,changes:{comparisons:[],advanced_trusted_reference:signed,
        input_check_cross_field:"block",output_check_cross_field:"flag",
        // Independent PII still runs and reports the timestamp/identifier;
        // this tenant explicitly chooses Flag rather than default Block.
        input_check_pii:"flag",output_check_pii:"flag"},
    }});
    expect(saved.status()).toBe(200);
    const live = await summary(request,headers);
    expect(live.revision).toBe(prior.revision+1);
    expect(live.advanced_trusted_reference_status).toHaveLength(3);
    for (const [name] of definitions) {
      expect(live.advanced_trusted_reference_status.find(r=>r.rule_id==="browser_"+name).runtime_integrity)
        .toBe("accepted");
    }
    await connect(page);
    for (const [name] of definitions) {
      await expect(page.locator("#trusted-reference-status")).toContainText("browser_"+name);
      await page.locator("#trusted-reference-status").getByRole("button",{
        name:"Review output action for host reference browser_"+name,
      }).click();
      await expect(page.locator('tr.rule-row[data-rule="business.browser_'+name+'"] [data-stage="output"]'))
        .toBeFocused();
    }
    const baseline = {dose_mg:700,administered_at:"2026-11-01T13:00:00Z",invoice_vendor_id:"VENDOR1"};
    for (const [stage,action] of [["input","block"],["output","flag"]]) {
      const check = async payload => {
        const response = await request.post("/v1/checks/"+stage,{headers,data:{
          application_id:"demo7",content_format:"json",content:JSON.stringify(payload),
        }});
        expect(response.status()).toBe(200);
        return response.json();
      };
      const clean = await check(baseline);
      const cleanEvidence = JSON.stringify({
        stage,
        evaluation_status: clean.evaluation_status,
        findings: clean.findings.map(f => ({rule_id: f.rule_id, category: f.category})),
        checker_results: clean.checker_results.map(c => ({
          checker_id: c.checker_id, status: c.status, error_code: c.error_code,
        })),
      });
      expect(clean.evaluation_status, cleanEvidence).toBe("complete");
      // The ordinary PII detector correctly continues finding timestamp and
      // identifier candidates. A passing trusted reference is NOT a privacy
      // bypass; the tenant chose Flag for independent PII, not Off or Allow.
      expect(clean.effective_action, cleanEvidence).toBe("flag");
      expect(clean.checker_results.find(c => c.checker_id === "pii")?.status).toBe("flagged");
      expect(clean.findings.some(f => f.rule_id?.startsWith("pii."))).toBe(true);
      expect(clean.findings.filter(f => f.rule_id?.startsWith("business.browser_"))).toEqual([]);
      for (const [name,,,,bad] of definitions) {
        const failed = await check({...baseline,...bad});
        expect(failed.evaluation_status).toBe("complete");
        expect(failed.effective_action).toBe(action);
        expect(failed.findings.filter(f=>f.rule_id?.startsWith("business.browser_")).map(f=>f.rule_id))
          .toEqual(["business.browser_"+name]);
      }
    }
  } finally {
    if (reset) expect((await request.post("/v1/tenants/current/reset",{headers,data:{}})).status()).toBe(200);
  }
});
