/* Tenant-owned comparison drafts; the existing revisioned save is authoritative. */
(() => {
  "use strict";
  let original = [], draft = [], editing = null, available = false;
  const copy = value => JSON.parse(JSON.stringify(value));
  const element = (tag, text) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    return node;
  };
  function delta() {
    return available && JSON.stringify(original) !== JSON.stringify(draft) ? copy(draft) : null;
  }
  function actions(stage, current) {
    const result = {...current};
    for (const item of original) {
      const kept = draft.find(rule => rule.rule_id === item.rule_id);
      if (!kept || !kept.stages.includes(stage)) delete result["business." + item.rule_id];
    }
    return result;
  }
  function render(host, tenant, enabled, changed) {
    original = Array.isArray(tenant.comparisons) ? copy(tenant.comparisons) : [];
    draft = copy(original); editing = null;
    available = enabled && Array.isArray(tenant.comparisons);
    host.replaceChildren(); host.hidden = !enabled;
    if (!enabled) return;
    host.append(element("h3", "Field comparison rules"));
    host.append(element("p", "Create, edit or remove number and calendar-date comparisons on your record. Apply a draft here, then Save checks. A rule runs only on its selected stages where Field comparisons is on. If no rules remain on a stage, turn that check off there before saving."));
    if (!available) {
      host.append(element("p", "Comparison definitions could not be loaded. Reconnect before editing."));
      return;
    }
    const summary = element("p"); summary.id = "comparison-draft-summary"; summary.setAttribute("role", "status"); summary.setAttribute("aria-live", "polite");
    const limitWarning = element("p"); limitWarning.id = "comparison-structure-warning";
    limitWarning.setAttribute("role", "status"); limitWarning.setAttribute("aria-live", "polite");
    limitWarning.hidden = true;
    const list = element("ul"); list.id = "comparison-drafts";
    const form = element("form"); form.id = "comparison-form";
    const controls = {};
    function field(name, label, choices) {
      const wrap = element("div"), control = element(choices ? "select" : "input");
      control.id = "comparison-" + name;
      const caption = element("label", label); caption.htmlFor = control.id;
      if (choices) for (const [value, text] of choices) {
        const option = element("option", text); option.value = value; control.append(option);
      } else { control.type = "text"; control.required = true; control.autocomplete = "off"; }
      wrap.append(caption, control); form.append(wrap); controls[name] = control;
      return control;
    }
    field("id", "Comparison ID").maxLength = 80;
    controls.id.pattern = "[a-zA-Z0-9_.\\-]{1,80}";
    field("left", "Left JSON pointer").maxLength = 200;
    field("operator", "Comparison", [["le", "≤ at most"], ["lt", "< less than"], ["eq", "= equal"], ["ne", "≠ different"], ["ge", "≥ at least"], ["gt", "> greater than"]]);
    field("right", "Right JSON pointer").maxLength = 200;
    field("type", "Value type", [["number", "Number"], ["date", "Calendar date (YYYY-MM-DD)"]]);
    const stages = element("fieldset"); stages.append(element("legend", "Run this comparison on"));
    for (const stage of ["input", "output"]) {
      const control = element("input"); control.id = "comparison-stage-" + stage; control.type = "checkbox"; control.checked = true;
      const label = element("label", stage === "input" ? "Input" : "Output"); label.htmlFor = control.id;
      stages.append(control, label); controls[stage] = control;
    }
    form.append(stages);
    const apply = element("button", "Add to pending changes"); apply.id = "comparison-apply"; apply.type = "submit";
    const cancel = element("button", "Cancel edit"); cancel.type = "button"; cancel.hidden = true;
    const message = element("p"); message.id = "comparison-message"; message.setAttribute("role", "status");
    form.append(apply, cancel, message); host.append(summary, limitWarning, list, form);
    // A customer with hundreds of ordinary comparisons needs a usable import path,
    // not 500 manual Add clicks or a hidden API. Import changes only the draft;
    // the existing revisioned Save checks and shared Engine remain authoritative.
    const bulk = element("details"); bulk.id = "comparison-bulk-panel";
    bulk.append(element("summary", "Bulk JSON import / export (30–500+ comparisons)"));
    bulk.append(element("p", "Paste a JSON array of ordinary comparison definitions. Import REPLACES only the unsaved comparison draft, never other validation families. Review the count, configure Field comparisons for the selected stages, then select Save checks. The API enforces the tenant record size; there is no arbitrary comparison-count cap."));
    const bulkLabel = element("label", "Comparison definitions as JSON array");
    bulkLabel.htmlFor = "comparison-bulk-json";
    const bulkJson = element("textarea"); bulkJson.id = "comparison-bulk-json";
    bulkJson.rows = 7; bulkJson.maxLength = 512000;
    bulkJson.setAttribute("spellcheck", "false");
    const exportDraft = element("button", "Load current draft into JSON box");
    exportDraft.type = "button"; exportDraft.id = "comparison-bulk-export";
    const acknowledgeLabel = element("label");
    const acknowledge = element("input"); acknowledge.type = "checkbox"; acknowledge.id = "comparison-bulk-confirm";
    acknowledgeLabel.append(acknowledge, document.createTextNode(" I understand this replaces the entire ordinary comparison draft; nothing is saved until Save checks."));
    const importDraft = element("button", "Stage JSON replacement");
    importDraft.type = "button"; importDraft.id = "comparison-bulk-import";
    const bulkStatus = element("p"); bulkStatus.id = "comparison-bulk-status";
    bulkStatus.setAttribute("role", "status"); bulkStatus.setAttribute("aria-live", "polite");
    bulk.append(bulkLabel, bulkJson, exportDraft, acknowledgeLabel, importDraft, bulkStatus);
    host.insertBefore(bulk, form);
    exportDraft.addEventListener("click", () => {
      bulkJson.value = JSON.stringify(draft, null, 2);
      acknowledge.checked = false;
      bulkStatus.textContent = draft.length + " current draft comparisons loaded into the JSON box; not saved.";
    });
    function importRules(raw) {
      if (!Array.isArray(raw)) throw Error("Use a JSON array of comparison objects.");
      const allowed = new Set(["rule_id", "left", "right", "operator", "value_type", "stages"]);
      const operators = new Set(["lt", "le", "eq", "ge", "gt", "ne"]);
      const ids = new Set();
      const existing = new Set(original.map(rule => rule.rule_id));
      const otherIds = new Set((tenant.record_rules || [])
        .filter(rule => rule && typeof rule.rule_id === "string")
        .map(rule => rule.rule_id));
      return raw.map((rule, i) => {
        const fail = reason => { throw Error("Comparison " + (i + 1) + ": " + reason); };
        if (!rule || typeof rule !== "object" || Array.isArray(rule)) return fail("expected an object.");
        if (Object.keys(rule).some(key => !allowed.has(key))) return fail("unknown field; use only rule_id, left, right, operator, value_type and stages.");
        const id = rule.rule_id;
        if (typeof id !== "string" || !/^[A-Za-z0-9_.-]{1,80}$/.test(id)) return fail("invalid rule_id.");
        if (ids.has(id) || (!existing.has(id) && otherIds.has("business." + id))) return fail("duplicate or reserved rule_id.");
        ids.add(id);
        for (const name of ["left", "right"]) {
          const pointer = rule[name];
          if (typeof pointer !== "string" || pointer.length < 1 || pointer.length > 200
              || !pointer.startsWith("/") || /~(?![01])/.test(pointer))
            return fail("left/right must be absolute JSON pointers of at most 200 characters.");
        }
        if (!operators.has(rule.operator)) return fail("unknown comparison operator.");
        const type = rule.value_type === undefined ? "number" : rule.value_type;
        if (!["number", "date"].includes(type)) return fail("value_type must be number or date.");
        const stages = rule.stages === undefined ? ["input", "output"] : rule.stages;
        if (!Array.isArray(stages) || stages.length < 1 || stages.length > 2
            || new Set(stages).size !== stages.length
            || stages.some(stage => stage !== "input" && stage !== "output"))
          return fail("stages must include Input, Output, or both without duplicates.");
        return {rule_id: id, left: rule.left, right: rule.right,
                operator: rule.operator, value_type: type, stages};
      });
    }
    importDraft.addEventListener("click", () => {
      if (!acknowledge.checked) {
        bulkStatus.textContent = "Confirm replacement of the ordinary comparison draft first.";
        acknowledge.focus(); return;
      }
      try {
        if (bulkJson.value.length > 512000) throw Error("JSON exceeds the 512,000-character editor limit.");
        const imported = importRules(JSON.parse(bulkJson.value));
        draft = imported; clear(); draw(); changed();
        bulkStatus.textContent = imported.length + " comparison definitions staged. Review Input/Output stages and select Save checks to persist; no server change yet.";
        acknowledge.checked = false;
      } catch (error) {
        // Never write partial imports, or echo submitted pointers/values into UI.
        bulkStatus.textContent = error instanceof SyntaxError
          ? "Invalid JSON; provide a valid array of comparison objects."
          : error.message;
      }
    });
    function clear() {
      editing = null; form.reset(); controls.id.disabled = false;
      controls.input.checked = controls.output.checked = true;
      apply.textContent = "Add to pending changes"; cancel.hidden = true;
    }
    function draw() {
      const pending = JSON.stringify(original) !== JSON.stringify(draft);
      summary.textContent = `${draft.length} comparison${draft.length === 1 ? "" : "s"} configured; ${draft.filter(rule => rule.stages.includes("input")).length} Input, ${draft.filter(rule => rule.stages.includes("output")).length} Output.${pending ? " Unsaved changes — select Save checks to persist." : " No pending changes."}`;
      // The shared JSON decoder limits *every* object to 1,000 members,
      // not just the root. Count distinct children at each pointer prefix in
      // each stage. A pair of sibling maps with 501 fields each is valid;
      // 1,002 fields under one nested map cannot pass when fully populated.
      // This is advisory and never imposes a tenant comparison-count cap.
      const warnings = [];
      for (const stage of ["input", "output"]) {
        const scopes = new Map(); // JSON-pointer prefix -> unique direct children
        for (const rule of draft) {
          if (!rule.stages.includes(stage)) continue;
          for (const pointer of [rule.left, rule.right]) {
            const parts = pointer.slice(1).split("/").map(
              item => item.replace(/~1/g, "/").replace(/~0/g, "~"));
            let prefix = "";
            for (const part of parts) {
              if (!scopes.has(prefix)) scopes.set(prefix, new Set());
              scopes.get(prefix).add(part);
              // Use a length-encoded prefix so a literal '/' in one field
              // cannot alias a true path separator in another field.
              prefix += part.length + ":" + part;
            }
          }
        }
        const roots = scopes.get("");
        if (roots && roots.size > 1000)
          warnings.push(stage + " references " + roots.size + " distinct top-level fields");
        let largestNested = 0;
        for (const [prefix, children] of scopes) {
          if (prefix && children.size > largestNested) largestNested = children.size;
        }
        if (largestNested > 1000)
          warnings.push(stage + " references " + largestNested + " distinct fields within a nested object");
      }
      limitWarning.hidden = !warnings.length;
      limitWarning.textContent = warnings.length
        ? "JSON structure warning: " + warnings.join("; ") +
          ". The shared Engine accepts at most 1,000 keys in any one JSON object, including nested objects. A fully populated document with these fields cannot pass. Use nested objects with at most 1,000 distinct children per object (up to 32 levels), or split the stages; the draft has not been modified."
        : "";
      list.replaceChildren();
      if (!draft.length) list.append(element("li", "No number or date comparisons on this record."));
      for (const item of draft) {
        const row = element("li"); row.dataset.comparisonId = item.rule_id;
        row.append(element("span", item.rule_id + ": " + item.left + " " + item.operator + " " + item.right + " (" + item.value_type + "; " + item.stages.join(", ") + ") "));
        const edit = element("button", "Edit"); edit.type = "button"; edit.setAttribute("aria-label", "Edit comparison " + item.rule_id);
        edit.addEventListener("click", () => {
          editing = item.rule_id; controls.id.value = item.rule_id; controls.id.disabled = true;
          controls.left.value = item.left; controls.right.value = item.right;
          controls.operator.value = item.operator; controls.type.value = item.value_type;
          for (const stage of ["input", "output"]) controls[stage].checked = item.stages.includes(stage);
          apply.textContent = "Update pending comparison"; cancel.hidden = false; message.textContent = "";
          controls.left.focus();
        });
        const remove = element("button", "Remove"); remove.type = "button"; remove.setAttribute("aria-label", "Remove comparison " + item.rule_id);
        remove.addEventListener("click", () => {
          draft = draft.filter(rule => rule.rule_id !== item.rule_id);
          if (editing === item.rule_id) clear();
          draw(); changed(); message.textContent = "Removal pending. Save checks to apply it.";
        });
        row.append(edit, remove); list.append(row);
      }
    }
    cancel.addEventListener("click", () => { clear(); message.textContent = ""; });
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const id = controls.id.value;
      const selected = ["input", "output"].filter(stage => controls[stage].checked);
      if (!selected.length) { message.textContent = "Choose Input, Output, or both."; return; }
      for (const name of ["left", "right"]) {
        const value = controls[name].value;
        if (!value.startsWith("/") || /~(?![01])/.test(value)) {
          message.textContent = "Use absolute JSON pointers, for example /invoice/total. Escape ~ as ~0 and / as ~1 inside a field name.";
          controls[name].focus(); return;
        }
      }
      const duplicate = draft.some(rule => rule.rule_id === id && rule.rule_id !== editing) ||
        (editing !== id && !original.some(rule => rule.rule_id === id) &&
         (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id));
      if (duplicate) { message.textContent = "That comparison ID is already used. Choose a different ID."; return; }
      const item = {rule_id: id, left: controls.left.value, right: controls.right.value,
                    operator: controls.operator.value, value_type: controls.type.value, stages: selected};
      if (editing !== null) draft = draft.map(rule => rule.rule_id === editing ? item : rule);
      else draft.push(item);
      clear(); draw(); changed(); message.textContent = "Comparison pending. Save checks to apply it.";
    });
    draw();
  }
  window.comparisonEditor = {render, delta, actions};
})();
