/* Tenant-owned FC-031/032/033/034/036/037/041/046 arithmetic editor.
   Uses existing tenant revisioned Save checks and the single cross_field Engine. */
(() => {
  "use strict";
  const kinds = {
    invoice_line_subtotal: ["lines", "subtotal"],
    invoice_total: ["subtotal", "discount", "tax", "shipping", "total"],
    tax_tolerance: ["tax", "base", "rate", "tolerance"],
    discount_fraction_cap: ["discount", "eligible_subtotal", "max_fraction"],
    inventory_conservation: ["opening", "received", "shipped", "lost", "closing"],
    shipment_quantity_sum: ["lines", "total"],
    manufacturing_tolerance: ["measured", "nominal", "tolerance"],
    carbon_intensity: ["emissions", "output", "max_intensity"]
  };
  const clone = item => JSON.parse(JSON.stringify(item));
  const node = (tag, text) => {
    const n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    return n;
  };
  let original = [], draft = [], available = false;
  // Canonicalize field-object key order and Input/Output stage order only.
  // The order of RULES and all non-stage arrays remains meaningful.
  // This prevents no-op customer edits from incrementing the tenant revision.
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value !== null && typeof value === "object") {
      return Object.fromEntries(Object.keys(value).sort().map(key => [
        key, key === "stages" && Array.isArray(value[key])
          ? [...value[key]].sort()
          : canonical(value[key])
      ]));
    }
    return value;
  }
  function samePortfolio(a, b) {
    return a.length === b.length && a.every((rule, index) =>
      JSON.stringify(canonical(rule)) === JSON.stringify(canonical(b[index])));
  }
  // Summary is calculated over all editable tenant definitions, not filtered rows.
  // Rule payloads and JSON pointers must never be echoed in this status text.
  function pendingImpact() {
    const saved = new Map(original.map(rule => [rule.rule_id, rule]));
    const pendingIds = new Set(draft.map(rule => rule.rule_id));
    let added = 0, updated = 0, removed = 0;
    for (const rule of draft) {
      const before = saved.get(rule.rule_id);
      if (!before) added++;
      else if (!samePortfolio([before], [rule])) updated++;
    }
    for (const rule of original) if (!pendingIds.has(rule.rule_id)) removed++;
    return {added, updated, removed};
  }
  function previewText() {
    if (samePortfolio(original, draft))
      return "Saved arithmetic rules: " + original.length + ". No pending changes.";
    const {added, updated, removed} = pendingImpact();
    return "Unsaved changes — " + added + " added, " + updated + " updated, " +
      removed + " removed." + (added + updated + removed === 0 ? " Rule order changed." : "") +
      " Save checks to apply changes to this tenant.";
  }
  function delta() {
    return available && !samePortfolio(original, draft) ? clone(draft) : null;
  }
  function actions(stage, values) {
    const result = {...values};
    // Saved rule cleanup must remain linear for 500+ tenant comparisons.
    // Only this family's removed or stage-disabled IDs may be cleared;
    // leave other families and host-protected reference actions untouched.
    const active = new Set(draft.filter(rule => rule.stages.includes(stage)).map(rule => rule.rule_id));
    for (const previous of original) {
      if (!active.has(previous.rule_id)) delete result["business." + previous.rule_id];
    }
    return result;
  }
  function render(host, tenant, enabled, changed) {
    original = Array.isArray(tenant.advanced_arithmetic) ? clone(tenant.advanced_arithmetic) : [];
    draft = clone(original);
    available = enabled && Array.isArray(tenant.advanced_arithmetic);
    host.replaceChildren(); host.hidden = !enabled;
    if (!enabled) return;
    host.append(node("h3", "Advanced arithmetic comparisons — eight deterministic rule types"));
    const scope = node("p", "A synthetic rule example is not a clean whole-tenant witness. Running it executes every enabled check and comparison on that stage; unrelated rules can also report findings. Save checks controls authoritative stage actions.");
    scope.dataset.fieldExampleScope = "arithmetic"; host.append(scope);
    host.append(node("p", "Define exact arithmetic and conservation rules using JSON pointers. No model call or outside reference. Save checks to persist a draft on this tenant; switch Field comparisons on for each selected stage and choose the rule action in the unified table. Nested JSON objects and arrays are supported; use canonical zero-based array segments such as /invoices/0/total. The bounded JSON parser still limits array size and nesting."));
    if (!available) { host.append(node("p", "Arithmetic definitions are unavailable. Reconnect before editing.")); return; }
    // Display-only search of editable tenant definitions; protected operator-owned
    // references are outside this draft and never searched or exposed.
    const filterBar = node("div");
    const searchLabel = node("label", "Find arithmetic rule by ID, type or JSON pointer");
    searchLabel.htmlFor = "arithmetic-filter";
    const searchInput = node("input"); searchInput.id = "arithmetic-filter";
    searchInput.type = "search"; searchInput.maxLength = 160; searchInput.autocomplete = "off";
    const stageLabel = node("label", "Show arithmetic rules on stage");
    stageLabel.htmlFor = "arithmetic-filter-stage";
    const stageFilter = node("select"); stageFilter.id = "arithmetic-filter-stage";
    for (const [value, caption] of [["all", "All stages"], ["input", "Input"], ["output", "Output"]]) {
      const option = node("option", caption); option.value = value; stageFilter.append(option);
    }
    stageFilter.value = "all";
    filterBar.append(searchLabel, searchInput, stageLabel, stageFilter);
    const filterStatus = node("p"); filterStatus.id = "arithmetic-filter-status";
    filterStatus.setAttribute("role", "status"); filterStatus.setAttribute("aria-live", "polite");
    const preview = node("p"); preview.id = "arithmetic-draft-summary";
    preview.setAttribute("role", "status"); preview.setAttribute("aria-live", "polite");
    const list = node("ul"); list.id = "arithmetic-drafts";
    const form = node("form"); form.id = "arithmetic-form";
    const message = node("p"); message.id = "arithmetic-message"; message.setAttribute("role", "status");
    const controls = {};
    function field(id, caption, kind = "text") {
      const wrapper = node("div"), label = node("label", caption), input = node(kind === "select" ? "select" : "input");
      input.id = "arithmetic-" + id; label.htmlFor = input.id;
      if (kind !== "select") { input.type = "text"; input.required = true; input.maxLength = 200; input.autocomplete = "off"; }
      wrapper.append(label, input); form.append(wrapper); controls[id] = input;
      return input;
    }
    field("id", "Arithmetic rule ID").maxLength = 80;
    controls.id.pattern = "[a-z][a-z0-9_]{0,79}";
    const type = field("kind", "Arithmetic validation type", "select");
    for (const kind of Object.keys(kinds)) type.append(new Option(kind.replaceAll("_", " "), kind));
    const fields = node("div"); fields.id = "arithmetic-fields"; form.append(fields);
    const stageGroup = node("fieldset"); stageGroup.append(node("legend", "Evaluate on"));
    for (const stage of ["input", "output"]) {
      const input = node("input"); input.type = "checkbox"; input.id = "arithmetic-stage-" + stage; input.checked = true;
      const label = node("label", stage === "input" ? "Input" : "Output"); label.htmlFor = input.id;
      stageGroup.append(input, label); controls[stage] = input;
    }
    form.append(stageGroup);
    const apply = node("button", "Add pending arithmetic rule"); apply.id = "arithmetic-apply"; apply.type = "submit";
    const cancel = node("button", "Cancel edit"); cancel.type = "button"; cancel.hidden = true;
    // Customer-owned guided forms. No example changes the tenant or draft
    // until explicit Add pending and revisioned Save checks in the main UI.
    function fillExample(id, kind, pointers, example) {
      // Refuse both saved and removed-but-unsaved IDs, any pending family
      // collision, and host-protected IDs. Never overwrite a customer rule.
      if (original.some(rule => rule.rule_id === id) ||
          draft.some(rule => rule.rule_id === id) ||
          (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_arithmetic"))) {
        message.textContent = "Example rule ID already exists. Edit the existing rule or choose your own ID.";
        return;
      }
      clear();
      controls.id.value = id;
      type.value = kind;
      pointerFields(pointers);
      controls.input.checked = controls.output.checked = true;
      message.textContent = "Example filled, not added or saved. Select Add pending arithmetic rule, " +
        "then Save checks. Enable Field comparisons on Input/Output and select Block " +
        "to stop violations. Example JSON: " + JSON.stringify(example) + ".";
      controls.id.focus();
    }
    const sampleHelp = node("p",
      "Invoice line example: two lines, 2 × 40 plus 1 × 30, require subtotal 110. " +
      "Subtotal 100 violates; 110 satisfies this rule. Other enabled tenant rules also run.");
    sampleHelp.id = "arithmetic-sample-help";
    const sample = node("button", "Fill example: invoice line amounts must equal subtotal");
    sample.id = "arithmetic-sample"; sample.type = "button";
    sample.addEventListener("click", () => fillExample("invoice_lines_match_subtotal",
      "invoice_line_subtotal", {lines: "/invoice/lines", subtotal: "/invoice/subtotal"},
      {invoice: {lines: [{quantity: 2, unit_price: 40}, {quantity: 1, unit_price: 30}], subtotal: 100}}));
    const totalHelp = node("p",
      "Invoice total example: subtotal 100 minus discount 10 plus tax 8 and shipping 2 " +
      "must total 100. Total 99 violates; 100 satisfies this rule. No payment verification is implied.");
    totalHelp.id = "arithmetic-invoice-total-help";
    const totalSample = node("button", "Fill example: invoice total must include discount, tax and shipping");
    totalSample.id = "arithmetic-sample-invoice-total"; totalSample.type = "button";
    totalSample.addEventListener("click", () => fillExample("invoice_total_matches", "invoice_total",
      {subtotal: "/billing/subtotal", discount: "/billing/discount", tax: "/billing/tax",
       shipping: "/billing/shipping", total: "/billing/total"},
      {billing: {subtotal: 100, discount: 10, tax: 8, shipping: 2, total: 99}}));
    const inventoryHelp = node("p",
      "Inventory example: opening 50 plus received 20 minus shipped 15 and lost 5 " +
      "must leave closing 50. Closing 51 violates; 50 satisfies this rule. " +
      "No external stock ledger is consulted.");
    inventoryHelp.id = "arithmetic-inventory-help";
    const inventorySample = node("button", "Fill example: inventory movements must balance");
    inventorySample.id = "arithmetic-sample-inventory"; inventorySample.type = "button";
    inventorySample.addEventListener("click", () => fillExample("inventory_balances", "inventory_conservation",
      {opening: "/inventory/opening", received: "/inventory/received",
       shipped: "/inventory/shipped", lost: "/inventory/lost", closing: "/inventory/closing"},
      {inventory: {opening: 50, received: 20, shipped: 15, lost: 5, closing: 51}}));
    const shipmentHelp = node("p",
      "Shipment quantity example: three units in the first line plus two in the " +
      "second must equal five shipped units. Total 4 violates; 5 satisfies. " +
      "Only submitted line quantities are compared; no warehouse inventory lookup.");
    shipmentHelp.id = "arithmetic-shipment-help";
    const shipmentSample = node("button", "Fill example: shipment line quantities must equal total");
    shipmentSample.id = "arithmetic-sample-shipment"; shipmentSample.type = "button";
    shipmentSample.addEventListener("click", () => fillExample("shipment_quantity_balances",
      "shipment_quantity_sum", {lines: "/shipment/lines", total: "/shipment/total"},
      {shipment: {lines: [{quantity: 3}, {quantity: 2}], total: 4}}));
    const intensityHelp = node("p",
      "Carbon intensity example: 250 kg CO2e across 100 output units is 2.5 kg per unit, " +
      "above the submitted 2 kg-per-unit cap. Emissions 200 satisfies the cap. " +
      "Units must be standardized upstream; no external emissions data is attested.");
    intensityHelp.id = "arithmetic-intensity-help";
    const intensitySample = node("button", "Fill example: carbon intensity must not exceed cap");
    intensitySample.id = "arithmetic-sample-intensity"; intensitySample.type = "button";
    intensitySample.addEventListener("click", () => fillExample("carbon_intensity_within_limit",
      "carbon_intensity", {emissions: "/operations/emissions",
      output: "/operations/output", max_intensity: "/operations/max_intensity"},
      {operations: {emissions: 250, output: 100, max_intensity: 2}}));
    const taxHelp = node("p",
      "Tax tolerance example: expected tax is 100 × 0.08 = 8, with an allowed " +
      "difference of 0.50. Reported 7 violates; 7.5 satisfies. Rate must be " +
      "a fraction from 0 to 1; the rule checks submitted values, not a tax authority.");
    taxHelp.id = "arithmetic-tax-help";
    const taxSample = node("button", "Fill example: tax must match rate within tolerance");
    taxSample.id = "arithmetic-sample-tax"; taxSample.type = "button";
    taxSample.addEventListener("click", () => fillExample("tax_within_tolerance",
      "tax_tolerance", {tax: "/tax/charged", base: "/tax/base",
        rate: "/tax/rate", tolerance: "/tax/tolerance"},
      {tax: {charged: 7, base: 100, rate: 0.08, tolerance: 0.5}}));
    const discountHelp = node("p",
      "Discount fraction example: a 25 discount exceeds a maximum 20% of a " +
      "100 eligible subtotal. A discount of 20 satisfies. Eligible subtotal " +
      "must be positive; maximum fraction must be between 0 and 1.");
    discountHelp.id = "arithmetic-discount-help";
    const discountSample = node("button", "Fill example: discount must stay within eligible fraction");
    discountSample.id = "arithmetic-sample-discount"; discountSample.type = "button";
    discountSample.addEventListener("click", () => fillExample("discount_within_fraction",
      "discount_fraction_cap", {discount: "/discount/amount",
        eligible_subtotal: "/discount/eligible_subtotal",
        max_fraction: "/discount/max_fraction"},
      {discount: {amount: 25, eligible_subtotal: 100, max_fraction: 0.2}}));
    const manufacturingHelp = node("p",
      "Manufacturing tolerance example: measured 10.4 differs from nominal 10 by " +
      "0.4 and exceeds tolerance 0.2. Measured 10.1 satisfies. Units must " +
      "already be consistent; no external calibration source is consulted.");
    manufacturingHelp.id = "arithmetic-manufacturing-help";
    const manufacturingSample = node("button", "Fill example: measurement must stay within tolerance");
    manufacturingSample.id = "arithmetic-sample-manufacturing"; manufacturingSample.type = "button";
    manufacturingSample.addEventListener("click", () => fillExample("measurement_within_tolerance",
      "manufacturing_tolerance", {measured: "/quality/measured",
        nominal: "/quality/nominal", tolerance: "/quality/tolerance"},
      {quality: {measured: 10.4, nominal: 10, tolerance: 0.2}}));
    form.append(sampleHelp, sample, totalHelp, totalSample, inventoryHelp, inventorySample,
      shipmentHelp, shipmentSample, intensityHelp, intensitySample,
      taxHelp, taxSample, discountHelp, discountSample,
      manufacturingHelp, manufacturingSample);
    form.append(apply, cancel, message); host.append(filterBar, filterStatus, preview, list, form);
    // The same revisioned tenant Save checks path supports hundreds of rules.
    // Bulk import updates ONLY this unsaved arithmetic draft, never the server
    // or another comparison family; invalid input cannot partially replace it.
    const bulk = node("details"); bulk.id = "arithmetic-bulk-panel";
    bulk.append(node("summary", "Bulk JSON import / export arithmetic rules"));
    bulk.append(node("p", "Paste a JSON array of arithmetic rule definitions (rule_id, kind, fields, stages). Import replaces the ENTIRE unsaved arithmetic draft, including rules hidden by filters; it does not save, change actions, or access protected reference values. Review the change summary and select Save checks."));
    const bulkLabel = node("label", "Arithmetic definitions as JSON array");
    bulkLabel.htmlFor = "arithmetic-bulk-json";
    const bulkJson = node("textarea"); bulkJson.id = "arithmetic-bulk-json";
    bulkJson.rows = 7; bulkJson.maxLength = 512000;
    bulkJson.setAttribute("spellcheck", "false");
    const exportDraft = node("button", "Load arithmetic draft into JSON box");
    exportDraft.type = "button"; exportDraft.id = "arithmetic-bulk-export";
    const acknowledgeLabel = node("label");
    const acknowledge = node("input"); acknowledge.type = "checkbox";
    acknowledge.id = "arithmetic-bulk-confirm";
    acknowledgeLabel.append(acknowledge, document.createTextNode(
      " I understand this replaces all unsaved arithmetic rules, not only filtered rules; Save checks is still required."));
    const importDraft = node("button", "Stage arithmetic JSON replacement");
    importDraft.type = "button"; importDraft.id = "arithmetic-bulk-import";
    const bulkStatus = node("p"); bulkStatus.id = "arithmetic-bulk-status";
    bulkStatus.setAttribute("role", "status"); bulkStatus.setAttribute("aria-live", "polite");
    bulk.append(bulkLabel, bulkJson, exportDraft, acknowledgeLabel, importDraft, bulkStatus);
    host.insertBefore(bulk, form);
    exportDraft.addEventListener("click", () => {
      bulkJson.value = JSON.stringify(draft, null, 2);
      acknowledge.checked = false;
      bulkStatus.textContent = draft.length + " arithmetic rules loaded into JSON box; not saved.";
    });
    function importRules(raw) {
      if (!Array.isArray(raw)) throw Error("Use a JSON array of arithmetic rule objects.");
      const ids = new Set();
      const savedIds = new Set(original.map(rule => rule.rule_id));
      const reserved = new Set((tenant.record_rules || [])
        .filter(rule => rule && typeof rule.rule_id === "string")
        .map(rule => rule.rule_id));
      return raw.map((rule, index) => {
        const fail = reason => { throw Error("Arithmetic rule " + (index + 1) + ": " + reason); };
        if (!rule || typeof rule !== "object" || Array.isArray(rule))
          return fail("expected an object.");
        if (Object.keys(rule).some(key => !["rule_id", "kind", "fields", "stages"].includes(key)))
          return fail("unknown rule field.");
        const id = rule.rule_id;
        if (typeof id !== "string" || !/^[a-z][a-z0-9_]{0,79}$/.test(id))
          return fail("invalid rule_id (lowercase, up to 80 characters).");
        if (ids.has(id) || (!savedIds.has(id) && reserved.has("business." + id)) ||
            (window.comparisonEditor &&
             window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_arithmetic")))
          return fail("duplicate or reserved rule_id, including other family drafts.");
        if (typeof rule.kind !== "string" || !Object.prototype.hasOwnProperty.call(kinds, rule.kind))
          return fail("unknown arithmetic rule kind.");
        if (!rule.fields || typeof rule.fields !== "object" || Array.isArray(rule.fields) ||
            Object.keys(rule.fields).length !== kinds[rule.kind].length ||
            kinds[rule.kind].some(key => !Object.prototype.hasOwnProperty.call(rule.fields, key)))
          return fail("fields must match the selected arithmetic kind exactly.");
        const fields = {}, decoded = [];
        for (const key of kinds[rule.kind]) {
          const value = rule.fields[key];
          if (typeof value !== "string" || value.length > 200 || !value.startsWith("/") ||
              /~(?![01])/.test(value))
            return fail("fields require absolute JSON pointers of at most 200 characters.");
          const path = value.slice(1).split("/").map(part =>
            part.replaceAll("~1", "/").replaceAll("~0", "~"));
          if (path.some(part => !part) || decoded.some(other =>
              other.every((part, i) => part === path[i]) ||
              path.every((part, i) => part === other[i])))
            return fail("field pointers must be nonempty, distinct and nonoverlapping.");
          decoded.push(path); fields[key] = value;
        }
        const stages = rule.stages === undefined ? ["input", "output"] : rule.stages;
        if (!Array.isArray(stages) || !stages.length || stages.length > 2 ||
            new Set(stages).size !== stages.length ||
            stages.some(stage => stage !== "input" && stage !== "output"))
          return fail("stages must contain Input, Output, or both, without duplicates.");
        ids.add(id);
        return {rule_id: id, kind: rule.kind, fields, stages};
      });
    }
    importDraft.addEventListener("click", () => {
      if (!acknowledge.checked) {
        bulkStatus.textContent = "Confirm replacement of the entire arithmetic draft first.";
        acknowledge.focus(); return;
      }
      try {
        if (bulkJson.value.length > 512000)
          throw Error("JSON exceeds the 512,000-character editor limit.");
        const imported = importRules(JSON.parse(bulkJson.value));
        draft = imported;
        clear(); draw(); changed();
        acknowledge.checked = false;
        bulkStatus.textContent = imported.length +
          " arithmetic rules staged; review Input/Output and pending changes, then select Save checks. No server change yet.";
      } catch (error) {
        // Do not echo attacker-provided JSON or customer field paths in errors.
        bulkStatus.textContent = error instanceof SyntaxError
          ? "Invalid JSON; provide an array of arithmetic rule objects."
          : error.message;
      }
    });
    searchInput.addEventListener("input", draw);
    stageFilter.addEventListener("change", draw);
    let editing = null;
    function pointerFields(existing = {}) {
      fields.replaceChildren();
      for (const key of kinds[type.value]) {
        const div = node("div"), label = node("label", key.replaceAll("_", " ") + " JSON pointer");
        const control = node("input");
        control.id = "arithmetic-field-" + key; control.type = "text"; control.required = true;
        control.maxLength = 200; control.autocomplete = "off"; control.placeholder = "/" + key;
        control.value = existing[key] || ""; label.htmlFor = control.id; div.append(label, control); fields.append(div);
      }
    }
    type.addEventListener("change", () => { pointerFields(); message.textContent = "Enter every declared field pointer for this rule type."; });
    function clear() {
      editing = null; form.reset(); controls.id.disabled = false; controls.input.checked = controls.output.checked = true;
      cancel.hidden = true; apply.textContent = "Add pending arithmetic rule"; pointerFields();
    }
    function draw() {
      list.replaceChildren();
      preview.textContent = previewText();
      const query = searchInput.value.trim().toLowerCase();
      const stage = stageFilter.value || "all";
      const visible = draft.filter(rule =>
        (stage === "all" || rule.stages.includes(stage)) &&
        (!query || rule.rule_id.toLowerCase().includes(query) ||
          rule.kind.toLowerCase().includes(query) ||
          Object.values(rule.fields || {}).some(pointer =>
            typeof pointer === "string" && pointer.toLowerCase().includes(query))));
      filterStatus.textContent = "Showing " + visible.length + " of " + draft.length + " editable arithmetic rules.";
      if (!visible.length) list.append(node("li", draft.length ?
        "No matching arithmetic rules. Clear the search or stage filter." :
        "No advanced arithmetic rules saved or drafted."));
      for (const rule of visible) {
        const item = node("li"); item.dataset.arithmeticId = rule.rule_id;
        item.append(node("span", rule.rule_id + " · " + rule.kind + " · " + rule.stages.join(", ") + " "));
        const edit = node("button", "Edit"); edit.type = "button"; edit.setAttribute("aria-label", "Edit arithmetic " + rule.rule_id);
        edit.addEventListener("click", () => {
          editing = rule.rule_id; controls.id.value = rule.rule_id; controls.id.disabled = true;
          type.value = rule.kind; pointerFields(rule.fields);
          for (const stage of ["input", "output"]) controls[stage].checked = rule.stages.includes(stage);
          cancel.hidden = false; apply.textContent = "Update pending arithmetic rule"; message.textContent = "";
          fields.querySelector("input")?.focus();
        });
        const remove = node("button", "Remove"); remove.type = "button"; remove.setAttribute("aria-label", "Remove arithmetic " + rule.rule_id);
        remove.addEventListener("click", () => {
          draft = draft.filter(entry => entry.rule_id !== rule.rule_id);
          if (editing === rule.rule_id) clear();
          draw(); changed(); message.textContent = "Arithmetic removal pending. Save checks to apply.";
        });
        item.append(edit, remove);
        // Saved rules already have synthetic witness and benign controls from
        // the authoritative record-rule generator. Use them in the same
        // authenticated Input/Output playground, never a second evaluator.
        const persisted = original.find(entry => entry.rule_id === rule.rule_id);
        const sameDefinition = persisted &&
          persisted.kind === rule.kind &&
          Object.keys(persisted.fields).length === Object.keys(rule.fields).length &&
          Object.entries(persisted.fields).every(([key, value]) => rule.fields[key] === value) &&
          persisted.stages.length === rule.stages.length &&
          persisted.stages.every(stage => rule.stages.includes(stage));
        if (persisted && !sameDefinition) {
          item.append(node("p", "Unsaved arithmetic rule changes: Save checks before using refreshed examples."));
        }
        const catalog = (tenant.record_rules || []).find(entry =>
          entry.rule_id === "business." + rule.rule_id);
        if (sameDefinition) {
          if (!catalog) {
            item.append(node("p", "No record-generated examples are available for this saved rule. Stage settings still apply; test an appropriate JSON document using the shared tenant Engine."));
          } else if (catalog.example_unavailable || catalog.look_alike_unavailable) {
            item.append(node("p", "Some synthetic examples are unavailable under the strict JSON admission or selected rule contract. The saved definition remains subject to stage check and rule actions; use shallower JSON pointers to restore example buttons."));
          }
          for (const stage of rule.stages) {
            // The saved parent check governs whether ANY comparison runs.
            // A pending dashboard change is not yet tenant-authoritative.
            // Keep the independent own-rule Off message when the check runs.
            const savedChecks = tenant[stage + "_checks"] || {};
            if (!savedChecks.cross_field || savedChecks.cross_field === "off") {
              item.append(node("p", "Saved " + stage + " Field comparisons check is Off. This rule is not evaluated on " + stage + " regardless of its own rule action; examples still test other enabled tenant checks."));
            } else if (tenant[stage + "_rule_actions"]?.["business." + rule.rule_id] === "off") {
              item.append(node("p", "Saved " + stage + " rule action is Off. Examples remain available for full-policy testing, but this rule will not report a finding on " + stage + " until you enable and Save checks."));
            }
            for (const [name, sample] of [["violating", catalog?.example],
                                          ["benign", catalog?.look_alike]]) {
              if (typeof sample !== "string") continue;
              const button = node("button", "Try " + name + " " + stage + " example");
              button.type = "button";
              button.setAttribute("aria-label", "Try " + name + " " + stage + " arithmetic " + rule.rule_id);
              button.addEventListener("click", () => {
                const content = document.getElementById(stage + "-content");
                const format = document.getElementById(stage + "-format");
                if (!content || !format || content.disabled) {
                  message.textContent = "Pass Input checks before preparing an Output example."; return;
                }
                format.value = "json";
                content.value = sample;
                content.dispatchEvent(new Event("input", {bubbles: true}));
                message.textContent = "Synthetic " + name + " " + stage +
                  " example loaded. Run " + stage + " checks through the existing tenant Engine.";
                content.focus();
              });
              item.append(button);
            }
          }
        }
        list.append(item);
      }
    }
    cancel.addEventListener("click", () => { clear(); message.textContent = ""; });
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const id = controls.id.value;
      const stages = ["input", "output"].filter(stage => controls[stage].checked);
      if (!stages.length) { message.textContent = "Choose Input, Output or both."; return; }
      const pointers = {}, segments = [];
      for (const key of kinds[type.value]) {
        const input = fields.querySelector('[id="arithmetic-field-' + key + '"]');
        const value = input.value;
        if (!value.startsWith("/") || /~(?![01])/.test(value)) {
          message.textContent = "Use absolute JSON pointers; escape ~ as ~0 and / as ~1.";
          input.focus(); return;
        }
        const path = value.slice(1).split("/").map(part => part.replaceAll("~1", "/").replaceAll("~0", "~"));
        if (path.some(part => !part)) {
          message.textContent = "Pointers cannot contain empty JSON Pointer segments.";
          input.focus(); return;
        }
        if (segments.some(prev => JSON.stringify(prev) === JSON.stringify(path) ||
            prev.every((part, i) => part === path[i]) || path.every((part, i) => part === prev[i]))) {
          message.textContent = "Arithmetic pointers must be distinct and nonoverlapping."; input.focus(); return;
        }
        segments.push(path); pointers[key] = value;
      }
      const existing = (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id);
      if (draft.some(rule => rule.rule_id === id && rule.rule_id !== editing) ||
          (editing === null && existing && !original.some(rule => rule.rule_id === id)) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_arithmetic"))) {
        message.textContent = "Rule ID already exists on the tenant."; return;
      }
      const rule = {rule_id: id, kind: type.value, fields: pointers, stages};
      if (editing !== null) draft = draft.map(old => old.rule_id === editing ? rule : old);
      else draft.push(rule);
      clear(); draw(); changed(); message.textContent = "Arithmetic rule pending. Save checks to persist.";
    });
    pointerFields(); draw();
  }
  window.arithmeticEditor = {render, delta, actions};
})();
