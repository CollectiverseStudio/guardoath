/* Tenant-owned conversion/time comparisons on the shared cross_field Engine.
   Private FX rate references are intentionally never sent to this editor. */
(() => {
  "use strict";
  const TYPES = {
    unit_load_kg_cap: ["load", "load_unit", "max_kg"],
    cold_chain_excursion: ["excursion_start", "excursion_end", "allowed_minutes"],
    lifecycle_chronology: ["order_at", "shipment_at", "delivery_at", "return_at"]
  };
  const clone = x => JSON.parse(JSON.stringify(x));
  const el = (tag, content) => {
    const n = document.createElement(tag);
    if (content !== undefined) n.textContent = content;
    return n;
  };
  let original = [], draft = [], enabled = false, protectedRules = [];
  function delta() {
    if (!enabled || JSON.stringify(original) === JSON.stringify(draft)) return null;
    // Scoped replacement leaves server-owned FX rules untouched.
    return protectedRules.length ? {editable: clone(draft)} : clone(draft);
  }
  function actions(stage, previous) {
    const result = {...previous};
    if (!enabled) return result;
    for (const item of original) {
      const kept = draft.find(rule => rule.rule_id === item.rule_id);
      if (!kept || !kept.stages.includes(stage)) delete result["business." + item.rule_id];
    }
    return result;
  }
  function render(host, tenant, canEdit, changed) {
    host.replaceChildren();
    host.hidden = !canEdit;
    enabled = canEdit && Array.isArray(tenant.advanced_conversion_time) &&
      Array.isArray(tenant.advanced_conversion_reference_status);
    original = Array.isArray(tenant.advanced_conversion_time) ? clone(tenant.advanced_conversion_time) : [];
    draft = clone(original);
    protectedRules = Array.isArray(tenant.advanced_conversion_reference_status) ?
      clone(tenant.advanced_conversion_reference_status) : [];
    if (!canEdit) return;
    host.append(el("h3", "Conversion and time comparisons"));
    const scope = el("p", "Rule examples demonstrate only the selected conversion rule. Running them checks ALL enabled tenant controls; unrelated comparisons may report additional findings. A synthetic benign sample is not proof of a clean whole-tenant result.");
    scope.dataset.fieldExampleScope = "conversion"; host.append(scope);
    host.append(el("p", "Configure unit-load, cold-chain and lifecycle chronology JSON checks. The existing tenant cross_field Engine and stage actions are authoritative. Nested object and array paths (for example /loads/0/load) are supported within strict JSON limits. FX conversion rates remain operator-owned and hidden here."));
    if (!enabled) {
      host.append(el("p", "Conversion definitions could not be loaded. Reconnect before editing."));
      return;
    }
    const references = el("section");
    references.id = "conversion-reference-status";
    references.append(el("h4", "FX conversion rules (protected)"));
    if (!protectedRules.length) {
      references.append(el("p", "No FX reference-backed rules configured."));
    } else {
      for (const item of protectedRules) {
        references.append(el("p", item.rule_id + " · " + item.kind + " · " +
          item.stages.join(", ") + " · FX reference configured: " +
          (item.reference_configured ? "yes" : "no")));
      }
      references.append(el("p", "Protected FX references remain read-only. Ordinary conversion/time rules can still be edited; Save checks submits only public definitions and the server preserves private FX rates under the tenant revision. Use authorized reference management for FX rules."));
    }
    host.append(references);
    const locked = false; // Protected FX reference rows are never in the editable draft.
    const list = el("ul"); list.id = "conversion-drafts";
    const form = el("form"); form.id = "conversion-form";
    const message = el("p"); message.id = "conversion-message";
    message.setAttribute("role", "status");
    const controls = {};
    function field(key, caption, options) {
      const wrap = el("div");
      const label = el("label", caption), input = el(options ? "select" : "input");
      input.id = "conversion-" + key;
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
    field("kind", "Conversion rule type", [
      ["unit_load_kg_cap", "Cargo mass does not exceed kg limit"],
      ["cold_chain_excursion", "Cold-chain excursion stays within minutes"],
      ["lifecycle_chronology", "Order / shipment / delivery / return time ordering"]
    ]);
    const paths = el("div"); paths.id = "conversion-fields"; form.append(paths);
    const eventRow = el("div"), eventLabel = el("label", "Optional return event JSON pointer"), eventPointer = el("input");
    eventPointer.id = "conversion-return-event-pointer"; eventPointer.type = "text";
    eventPointer.maxLength = 200; eventPointer.autocomplete = "off";
    eventPointer.placeholder = "/returns/latest"; eventLabel.htmlFor = eventPointer.id;
    eventRow.append(eventLabel, eventPointer,
      el("p", "Point to an optional return record, never to a parent of required order, shipment or delivery timestamps. Absent event: allowed; recorded incomplete return: finding."));
    form.append(eventRow);
    const stages = el("fieldset"); stages.append(el("legend", "Evaluate on"));
    for (const stage of ["input", "output"]) {
      const input = el("input"); input.id = "conversion-stage-" + stage;
      input.type = "checkbox"; input.checked = true;
      const label = el("label", stage === "input" ? "Input" : "Output");
      label.htmlFor = input.id; stages.append(input, label); controls[stage] = input;
    }
    form.append(stages);
    const apply = el("button", "Add pending conversion rule");
    apply.id = "conversion-apply"; apply.type = "submit";
    const cancel = el("button", "Cancel edit");
    cancel.id = "conversion-cancel"; cancel.type = "button"; cancel.hidden = true;
    form.append(apply, cancel, message);
    host.append(list, form);
    let editing = null;
    function drawFields(values = {}) {
      paths.replaceChildren();
      eventRow.hidden = controls.kind.value !== "lifecycle_chronology";
      if (eventRow.hidden) eventPointer.value = "";
      for (const key of TYPES[controls.kind.value]) {
        const div = el("div"), label = el("label", key + " JSON pointer"), input = el("input");
        input.type = "text"; input.required = true; input.maxLength = 200;
        input.autocomplete = "off"; input.id = "conversion-field-" + key;
        input.placeholder = "/" + key; input.value = values[key] || "";
        label.htmlFor = input.id; div.append(label, input); paths.append(div);
      }
    }
    controls.kind.addEventListener("change", () => {
      drawFields(); message.textContent = "Enter the JSON pointers required by this conversion rule.";
    });
    function clear() {
      editing = null; form.reset(); controls.id.disabled = false;
      controls.input.checked = controls.output.checked = true;
      apply.textContent = "Add pending conversion rule"; cancel.hidden = true;
      drawFields();
    }
    function draw() {
      list.replaceChildren();
      if (!draft.length) list.append(el("li", "No editable conversion/time rules on this tenant."));
      for (const rule of draft) {
        const item = el("li"); item.dataset.conversionId = rule.rule_id;
        item.append(el("span", rule.rule_id + " · " + rule.kind + " · " + rule.stages.join(", ") + " "));
        const edit = el("button", "Edit");
        edit.type = "button"; edit.setAttribute("aria-label", "Edit conversion " + rule.rule_id);
        edit.disabled = locked;
        edit.addEventListener("click", () => {
          editing = rule.rule_id;
          controls.id.value = rule.rule_id; controls.id.disabled = true;
          controls.kind.value = rule.kind; drawFields(rule.fields);
          eventPointer.value = rule.return_event_pointer || "";
          controls.input.checked = rule.stages.includes("input");
          controls.output.checked = rule.stages.includes("output");
          apply.textContent = "Update pending conversion rule"; cancel.hidden = false;
          message.textContent = "";
        });
        const remove = el("button", "Remove");
        remove.type = "button"; remove.disabled = locked;
        remove.setAttribute("aria-label", "Remove conversion " + rule.rule_id);
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
              button.setAttribute("aria-label", "Try " + label + " " + stage + " conversion " + rule.rule_id);
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
        message.textContent = "Protected FX references cannot be edited in this public rule editor.";
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
        const input = paths.querySelector('[id="conversion-field-' + key + '"]');
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
      let returnEvent = null;
      if (controls.kind.value === "lifecycle_chronology" && eventPointer.value.trim()) {
        const pointer = eventPointer.value.trim();
        const segments = str => str.slice(1).split("/").map(s =>
          s.replaceAll("~1", "/").replaceAll("~0", "~"));
        const event = pointer.startsWith("/") && !/~(?![01])/.test(pointer)
          ? segments(pointer) : [];
        const time = segments(values.return_at);
        if (!event.length || event.some(s => !s) || event.length >= time.length ||
            !event.every((s, i) => s === time[i])) {
          message.textContent = "Return event must be an absolute JSON pointer to a parent of return_at.";
          eventPointer.focus(); return;
        }
        if (["order_at", "shipment_at", "delivery_at"].some(role => {
          const required = segments(values[role]);
          return event.length <= required.length && event.every((s, i) => s === required[i]);
        })) {
          message.textContent = "Return event cannot contain mandatory order, shipment, or delivery timestamps.";
          eventPointer.focus(); return;
        }
        returnEvent = pointer;
      }
      const exists = (tenant.record_rules || []).some(r => r.rule_id === "business." + id);
      if (draft.some(x => x.rule_id === id && x.rule_id !== editing) ||
          (editing === null && exists && !original.some(x => x.rule_id === id))) {
        message.textContent = "Rule ID already exists on the tenant."; return;
      }
      const item = {rule_id:id, kind:controls.kind.value, fields:values, stages:selected};
      if (returnEvent !== null) item.return_event_pointer = returnEvent;
      if (editing !== null) draft = draft.map(x => x.rule_id === editing ? item : x);
      else draft.push(item);
      clear(); draw(); changed();
      message.textContent = "Conversion rule pending. Save checks to persist it.";
    });
    drawFields();
    for (const input of form.querySelectorAll("input, select, button")) input.disabled = false;
    draw();
  }
  window.conversionEditor = {render, delta, actions};
})();
