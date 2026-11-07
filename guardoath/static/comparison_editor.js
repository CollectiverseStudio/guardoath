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
    controls.id.pattern = "[a-zA-Z0-9_.-]{1,80}";
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
    form.append(apply, cancel, message); host.append(list, form);
    function clear() {
      editing = null; form.reset(); controls.id.disabled = false;
      controls.input.checked = controls.output.checked = true;
      apply.textContent = "Add to pending changes"; cancel.hidden = true;
    }
    function draw() {
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
