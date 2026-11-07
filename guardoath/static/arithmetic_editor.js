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
  function delta() {
    return available && JSON.stringify(original) !== JSON.stringify(draft) ? clone(draft) : null;
  }
  function actions(stage, values) {
    const result = {...values};
    for (const previous of original) {
      const existing = draft.find(item => item.rule_id === previous.rule_id);
      if (!existing || !existing.stages.includes(stage)) delete result["business." + previous.rule_id];
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
    host.append(node("p", "Define exact arithmetic and conservation rules using JSON pointers. No model call or outside reference. Save checks to persist a draft on this tenant; switch Field comparisons on for each selected stage and choose the rule action in the unified table. Numeric arrays or objects must match the chosen rule's expected structure."));
    if (!available) { host.append(node("p", "Arithmetic definitions are unavailable. Reconnect before editing.")); return; }
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
    form.append(apply, cancel, message); host.append(list, form);
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
      if (!draft.length) list.append(node("li", "No advanced arithmetic rules saved or drafted."));
      for (const rule of draft) {
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
        const saved = original.some(entry => entry.rule_id === rule.rule_id);
        const catalog = (tenant.record_rules || []).find(entry =>
          entry.rule_id === "business." + rule.rule_id);
        if (saved && catalog) {
          for (const stage of rule.stages) {
            for (const [name, sample] of [["violating", catalog.example],
                                          ["benign", catalog.look_alike]]) {
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
        if (path.some(part => !part || /^\d+$/.test(part))) {
          message.textContent = "Pointers must address named object fields, not array positions or empty segments.";
          input.focus(); return;
        }
        if (segments.some(prev => prev.join("/") === path.join("/") ||
            prev.every((part, i) => part === path[i]) || path.every((part, i) => part === prev[i]))) {
          message.textContent = "Arithmetic pointers must be distinct and nonoverlapping."; input.focus(); return;
        }
        segments.push(path); pointers[key] = value;
      }
      const existing = (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id);
      if (draft.some(rule => rule.rule_id === id && rule.rule_id !== editing) ||
          (editing === null && existing && !original.some(rule => rule.rule_id === id))) {
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
