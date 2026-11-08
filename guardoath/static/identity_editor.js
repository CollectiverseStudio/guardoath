/* Tenant-owned identity comparisons on existing cross_field Engine.
   Reference-backed rule values are intentionally never sent to this editor. */
(() => {
  "use strict";
  const TYPES = {
    order_id_match: ["order_id", "shipment_order_id"],
    serial_unique: ["serials"]
  };
  const clone = x => JSON.parse(JSON.stringify(x));
  const el = (tag, content) => {
    const n = document.createElement(tag);
    if (content !== undefined) n.textContent = content;
    return n;
  };
  let original = [], draft = [], enabled = false, protectedRules = [];
  function delta() {
    if (!enabled || protectedRules.length) return null;
    return JSON.stringify(original) === JSON.stringify(draft) ? null : clone(draft);
  }
  function actions(stage, previous) {
    const result = {...previous};
    if (!enabled || protectedRules.length) return result;
    for (const item of original) {
      const kept = draft.find(rule => rule.rule_id === item.rule_id);
      if (!kept || !kept.stages.includes(stage)) delete result["business." + item.rule_id];
    }
    return result;
  }
  function render(host, tenant, canEdit, changed) {
    host.replaceChildren();
    host.hidden = !canEdit;
    enabled = canEdit && Array.isArray(tenant.advanced_identity) &&
      Array.isArray(tenant.advanced_identity_reference_status);
    original = Array.isArray(tenant.advanced_identity) ? clone(tenant.advanced_identity) : [];
    draft = clone(original);
    protectedRules = Array.isArray(tenant.advanced_identity_reference_status) ?
      clone(tenant.advanced_identity_reference_status) : [];
    if (!canEdit) return;
    host.append(el("h3", "Identity and collection comparisons"));
    const scope = el("p", "Rule examples demonstrate only the selected identity rule. Running them checks ALL enabled tenant controls, including unrelated comparisons; additional findings or schema errors may appear. No reference-backed tenant values are exposed.");
    scope.dataset.fieldExampleScope = "identity"; host.append(scope);
    host.append(el("p", "Configure exact order-ID equality and unique serial-number checks for JSON. Each saved rule uses the same tenant cross_field Engine and Input/Output rule actions. Nested object and array paths (for example /shipments/0/order_id) are supported within strict JSON limits. Trusted quarantine/scope reference sets are never exposed or editable here."));
    if (!enabled) {
      host.append(el("p", "Identity definitions could not be loaded. Reconnect before editing."));
      return;
    }
    const references = el("section");
    references.id = "identity-reference-status";
    references.append(el("h4", "Reference-backed identity rules (protected)"));
    if (!protectedRules.length) {
      references.append(el("p", "No reference-backed identity rules configured."));
    } else {
      for (const item of protectedRules) {
        references.append(el("p", item.rule_id + " · " + item.kind + " · " +
          item.stages.join(", ") + " · trusted reference configured: " +
          (item.reference_configured ? "yes" : "no")));
      }
      references.append(el("p", "Reference-backed rules are read-only here. All identity edits are locked while these rules exist: deleting or overwriting an unexposed trusted value through a full-array save would be unsafe. Use an authorized reference-management workflow."));
    }
    host.append(references);
    const locked = protectedRules.length > 0;
    const list = el("ul"); list.id = "identity-drafts";
    const form = el("form"); form.id = "identity-form";
    const message = el("p"); message.id = "identity-message";
    message.setAttribute("role", "status");
    const controls = {};
    function field(key, caption, options) {
      const wrap = el("div");
      const label = el("label", caption), input = el(options ? "select" : "input");
      input.id = "identity-" + key;
      label.htmlFor = input.id;
      if (options) for (const [value, text] of options) input.append(new Option(text, value));
      else {
        input.type = "text"; input.required = true; input.maxLength = 200;
        input.autocomplete = "off";
      }
      controls[key] = input; wrap.append(label, input); form.append(wrap);
      return input;
    }
    field("id", "Rule ID").maxLength = 80;
    controls.id.pattern = "[a-z][a-z0-9_]{0,79}";
    field("kind", "Identity rule type", [
      ["order_id_match", "Order ID matches shipment order ID"],
      ["serial_unique", "Serial values must be unique"]
    ]);
    const paths = el("div"); paths.id = "identity-fields"; form.append(paths);
    const stages = el("fieldset"); stages.append(el("legend", "Evaluate on"));
    for (const stage of ["input", "output"]) {
      const input = el("input"); input.id = "identity-stage-" + stage;
      input.type = "checkbox"; input.checked = true;
      const label = el("label", stage === "input" ? "Input" : "Output");
      label.htmlFor = input.id; stages.append(input, label); controls[stage] = input;
    }
    form.append(stages);
    const apply = el("button", "Add pending identity rule");
    apply.id = "identity-apply"; apply.type = "submit";
    const cancel = el("button", "Cancel edit");
    cancel.id = "identity-cancel"; cancel.type = "button"; cancel.hidden = true;
    form.append(apply, cancel, message);
    host.append(list, form);
    let editing = null;
    function drawFields(values = {}) {
      paths.replaceChildren();
      for (const key of TYPES[controls.kind.value]) {
        const div = el("div"), label = el("label", key + " JSON pointer"), input = el("input");
        input.type = "text"; input.required = true; input.maxLength = 200;
        input.autocomplete = "off"; input.id = "identity-field-" + key;
        input.placeholder = "/" + key; input.value = values[key] || "";
        label.htmlFor = input.id; div.append(label, input); paths.append(div);
      }
    }
    controls.kind.addEventListener("change", () => {
      drawFields(); message.textContent = "Enter the JSON pointers required by this identity rule.";
    });
    function clear() {
      editing = null; form.reset(); controls.id.disabled = false;
      controls.input.checked = controls.output.checked = true;
      apply.textContent = "Add pending identity rule"; cancel.hidden = true;
      drawFields();
    }
    function draw() {
      list.replaceChildren();
      if (!draft.length) list.append(el("li", "No editable identity rules on this tenant."));
      for (const rule of draft) {
        const item = el("li"); item.dataset.identityId = rule.rule_id;
        item.append(el("span", rule.rule_id + " · " + rule.kind + " · " + rule.stages.join(", ") + " "));
        const edit = el("button", "Edit");
        edit.type = "button"; edit.setAttribute("aria-label", "Edit identity " + rule.rule_id);
        edit.disabled = locked;
        edit.addEventListener("click", () => {
          editing = rule.rule_id;
          controls.id.value = rule.rule_id; controls.id.disabled = true;
          controls.kind.value = rule.kind; drawFields(rule.fields);
          controls.input.checked = rule.stages.includes("input");
          controls.output.checked = rule.stages.includes("output");
          apply.textContent = "Update pending identity rule"; cancel.hidden = false;
          message.textContent = "";
        });
        const remove = el("button", "Remove");
        remove.type = "button"; remove.disabled = locked;
        remove.setAttribute("aria-label", "Remove identity " + rule.rule_id);
        remove.addEventListener("click", () => {
          draft = draft.filter(item => item.rule_id !== rule.rule_id);
          if (editing === rule.rule_id) clear();
          draw(); changed(); message.textContent = "Removal pending. Save checks to apply it.";
        });
        item.append(edit, remove);
        const saved = original.some(x => x.rule_id === rule.rule_id);
        const example = (tenant.record_rules || []).find(x => x.rule_id === "business." + rule.rule_id);
        if (saved && example) {
          for (const stage of rule.stages) {
            for (const [label, sample] of [["violating", example.example],
                                           ["benign", example.look_alike]]) {
              if (typeof sample !== "string") continue;
              const button = el("button", "Try " + label + " " + stage + " example");
              button.type = "button";
              button.setAttribute("aria-label", "Try " + label + " " + stage + " identity " + rule.rule_id);
              button.addEventListener("click", () => {
                const content = document.getElementById(stage + "-content");
                const format = document.getElementById(stage + "-format");
                if (!content || !format || content.disabled) {
                  message.textContent = "Pass Input checks before preparing an Output example.";
                  return;
                }
                format.value = "json"; content.value = sample;
                content.dispatchEvent(new Event("input", {bubbles: true}));
                message.textContent = "Synthetic " + label + " " + stage +
                  " example loaded. Run the existing tenant checks.";
                content.focus();
              });
              item.append(button);
            }
          }
        }
        list.append(item);
      }
    }
    cancel.addEventListener("click", () => {clear(); message.textContent = "";});
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (locked) {
        message.textContent = "Protected reference-backed rules prevent editing this identity array.";
        return;
      }
      if (!form.reportValidity()) return;
      const id = controls.id.value;
      const selected = ["input", "output"].filter(s => controls[s].checked);
      if (!selected.length) {
        message.textContent = "Choose Input, Output, or both."; return;
      }
      const values = {}, pathsSeen = [];
      for (const key of TYPES[controls.kind.value]) {
        const input = paths.querySelector('[id="identity-field-' + key + '"]');
        const pointer = input.value;
        if (!pointer.startsWith("/") || /~(?![01])/.test(pointer)) {
          message.textContent = "Use absolute JSON pointers and valid ~0/~1 escapes.";
          input.focus(); return;
        }
        const segments = pointer.slice(1).split("/").map(x =>
          x.replaceAll("~1", "/").replaceAll("~0", "~"));
        if (segments.some(x => !x) ||
            pathsSeen.some(p => JSON.stringify(p) === JSON.stringify(segments) ||
              p.every((x,i) => x === segments[i]) ||
              segments.every((x,i) => x === p[i]))) {
          message.textContent = "Pointers must have nonempty segments and be distinct and nonoverlapping.";
          input.focus(); return;
        }
        pathsSeen.push(segments); values[key] = pointer;
      }
      const exists = (tenant.record_rules || []).some(r => r.rule_id === "business." + id);
      if (draft.some(x => x.rule_id === id && x.rule_id !== editing) ||
          (editing === null && exists && !original.some(x => x.rule_id === id))) {
        message.textContent = "Rule ID already exists on the tenant."; return;
      }
      const item = {rule_id:id, kind:controls.kind.value, fields:values, stages:selected,
                    trusted_values:[]};
      if (editing !== null) draft = draft.map(x => x.rule_id === editing ? item : x);
      else draft.push(item);
      clear(); draw(); changed();
      message.textContent = "Identity rule pending. Save checks to persist it.";
    });
    drawFields();
    for (const input of form.querySelectorAll("input, select, button")) input.disabled = locked;
    draw();
  }
  window.identityEditor = {render, delta, actions};
})();
