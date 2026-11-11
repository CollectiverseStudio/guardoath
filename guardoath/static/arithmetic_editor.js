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
    // Guided, deterministic structured-JSON example. The sample is form-only:
    // the authoritative tenant must be updated by Add pending and Save checks.
    const sampleHelp = node("p",
      "Invoice example: two lines, 2 × 40 plus 1 × 30, require a subtotal of 110. " +
      "A subtotal of 100 violates the rule; 110 is valid. All other enabled tenant " +
      "checks still run when using its synthetic witness.");
    sampleHelp.id = "arithmetic-sample-help";
    const sample = node("button", "Fill example: invoice line amounts must equal subtotal");
    sample.id = "arithmetic-sample"; sample.type = "button";
    sample.addEventListener("click", () => {
      const id = "invoice_lines_match_subtotal";
      // No overwrites, including a saved rule removed from the current draft,
      // a protected rule, or an ID held by a different family pending Save.
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
      type.value = "invoice_line_subtotal";
      pointerFields({lines: "/invoice/lines", subtotal: "/invoice/subtotal"});
      controls.input.checked = controls.output.checked = true;
      message.textContent = "Example filled, not added or saved. Select Add pending arithmetic rule, " +
        "then Save checks. Enable Field comparisons on Input/Output and select Block " +
        "to stop violations. Example JSON: {\"invoice\":{\"lines\":[{\"quantity\":2,\"unit_price\":40}," +
        "{\"quantity\":1,\"unit_price\":30}],\"subtotal\":100}}.";
      controls.id.focus();
    });
    form.append(sampleHelp, sample);
    form.append(apply, cancel, message); host.append(filterBar, filterStatus, preview, list, form);
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
