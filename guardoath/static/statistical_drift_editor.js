/* Owner-only, revisioned PSI rule editor; never trusts caller-supplied chat as a reference. */
(() => {
  "use strict";
  let original = [], draft = [], available = false, generation = 0;
  const clone = value => JSON.parse(JSON.stringify(value));
  const node = (tag, text) => {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    return item;
  };
  function delta() {
    return available && JSON.stringify(original) !== JSON.stringify(draft) ? clone(draft) : null;
  }
  // The PSI reference is owner-only and deliberately absent from the public
  // application summary. Answer whether a pending draft changes a given stage
  // using the original owner-scoped record, without exposing any histogram.
  function affectsStage(stage) {
    if (!available || !["input", "output"].includes(stage)) return false;
    const selected = rules => rules.filter(rule => rule.stages.includes(stage));
    return JSON.stringify(selected(original)) !== JSON.stringify(selected(draft));
  }
  function actions(stage, current) {
    const result = {...current};
    for (const item of original) {
      const kept = draft.find(rule => rule.rule_id === item.rule_id);
      if (!kept || !kept.stages.includes(stage)) delete result["business." + item.rule_id];
    }
    return result;
  }
  function render(host, tenant, canEdit, readOwnerRules, changed) {
    const stamp = ++generation;
    available = false; original = []; draft = [];
    host.replaceChildren();
    host.hidden = !canEdit;
    if (!canEdit) return;
    host.append(node("h3", "Statistical drift (PSI) batch rules"));
    host.append(node("p", "Configure a versioned, tenant-supplied reference histogram for bounded JSON batches. This is a numerical distribution comparison, not a semantic AI safety score, automatic streaming monitor or independently verified reference source. Turn on Field comparisons for the desired Input/Output stage and choose its action in the unified rule table."));
    const status = node("p", "Loading the authenticated owner configuration…");
    status.id = "drift-editor-status";
    status.setAttribute("role", "status");
    host.append(status);
    // Existing application summary deliberately hides the private histogram.
    // Only a signed-in owner (or a demo tenant acting on its own demo record)
    // can read/edit values. A normal production API key must receive HTTP 403.
    Promise.resolve().then(readOwnerRules).then(data => {
      if (generation !== stamp) return;
      if (!data || data.revision !== tenant.revision || !Array.isArray(data.rules)) {
        status.textContent = "The tenant revision changed or the statistical reference could not be read. Reconnect to edit; nothing has been saved.";
        return;
      }
      original = clone(data.rules); draft = clone(original); available = true;
      status.textContent = "Owner-only reference configuration loaded. Changes are drafts until Save checks succeeds.";
      build(host, tenant, changed);
    }).catch(() => {
      if (generation === stamp) status.textContent =
        "The owner-only reference is unavailable. No values were loaded or changed. Sign in or reconnect.";
    });
  }
  function build(host, tenant, changed) {
    let editing = null;
    const list = node("ul"); list.id = "drift-drafts";
    const form = node("form"); form.id = "drift-form";
    const fields = {};
    function field(name, label, limit, initial) {
      const wrap = node("div"), input = node("input");
      input.type = "text"; input.required = true; input.id = "drift-" + name;
      input.maxLength = limit; input.autocomplete = "off";
      if (initial !== undefined) input.value = initial;
      const caption = node("label", label); caption.htmlFor = input.id;
      wrap.append(caption, input); form.append(wrap); fields[name] = input;
      return input;
    }
    field("id", "Rule ID (lowercase, stable)", 80).pattern = "[a-z][a-z0-9_]{0,79}";
    field("pointer", "JSON pointer to numeric observations array", 200);
    field("reference-id", "Tenant-supplied reference ID (not source attestation)", 100);
    field("reference-version", "Reference version", 80);
    field("edges", "Bin edges, comma separated (3–65 ascending numbers)", 1500);
    field("counts", "Reference bin counts, comma separated (2–64 whole numbers)", 900);
    field("threshold", "PSI threshold (greater than 0, at most 10)", 24);
    field("minimum", "Minimum observations (30–100000)", 6);
    const stages = node("fieldset"); stages.append(node("legend", "Evaluate on"));
    for (const stage of ["input", "output"]) {
      const select = node("input"); select.id = "drift-stage-" + stage;
      select.type = "checkbox"; select.checked = true;
      const label = node("label", stage === "input" ? "Input" : "Output");
      label.htmlFor = select.id; stages.append(select, label); fields[stage] = select;
    }
    form.append(stages);
    const submit = node("button", "Add to pending changes");
    submit.id = "drift-apply"; submit.type = "submit";
    const cancel = node("button", "Cancel edit");
    cancel.type = "button"; cancel.hidden = true;
    const preset = node("button", "Fill synthetic PSI sample");
    preset.type = "button"; preset.id = "drift-synthetic-preset";
    const run = node("button", "Load synthetic shifted batch into Input data");
    run.type = "button"; run.id = "drift-synthetic-input";
    const message = node("p"); message.id = "drift-message";
    message.setAttribute("role", "status");
    form.append(submit, cancel, preset, run, message);
    host.append(list, form);
    const decode = raw => raw.split(",").map(v => v.trim());
    function clear() {
      editing = null; form.reset(); fields.id.disabled = false;
      fields.input.checked = fields.output.checked = true;
      submit.textContent = "Add to pending changes"; cancel.hidden = true;
    }
    function draw() {
      list.replaceChildren();
      if (!draft.length) list.append(node("li", "No tenant PSI batch rules configured."));
      for (const item of draft) {
        const row = node("li"); row.dataset.driftId = item.rule_id;
        row.append(node("span", item.rule_id + " · " + item.observations_pointer +
          " · reference " + item.reference_id + "/" + item.reference_version +
          " · PSI > " + item.psi_threshold + " · " + item.stages.join(", ") + " "));
        const edit = node("button", "Edit"); edit.type = "button";
        edit.setAttribute("aria-label", "Edit PSI " + item.rule_id);
        edit.addEventListener("click", () => {
          editing = item.rule_id; fields.id.value = item.rule_id; fields.id.disabled = true;
          fields.pointer.value = item.observations_pointer;
          fields["reference-id"].value = item.reference_id;
          fields["reference-version"].value = item.reference_version;
          fields.edges.value = item.bin_edges.join(", ");
          fields.counts.value = item.counts.join(", ");
          fields.threshold.value = item.psi_threshold;
          fields.minimum.value = String(item.min_observations);
          for (const stage of ["input", "output"]) fields[stage].checked = item.stages.includes(stage);
          submit.textContent = "Update pending PSI rule"; cancel.hidden = false;
          message.textContent = ""; fields.pointer.focus();
        });
        const remove = node("button", "Remove"); remove.type = "button";
        remove.setAttribute("aria-label", "Remove PSI " + item.rule_id);
        remove.addEventListener("click", () => {
          draft = draft.filter(rule => rule.rule_id !== item.rule_id);
          if (editing === item.rule_id) clear();
          draw(); changed(); message.textContent = "Removal pending. Save checks to apply it.";
        });
        row.append(edit, remove); list.append(row);
      }
    }
    cancel.addEventListener("click", () => {clear(); message.textContent = "";});
    preset.addEventListener("click", () => {
      if (editing !== null) clear();
      fields.id.value = "ui_psi_demo";
      fields.pointer.value = "/income_samples";
      fields["reference-id"].value = "synthetic-not-attested";
      fields["reference-version"].value = "v1";
      fields.edges.value = "0, 10, 20, 30";
      fields.counts.value = "10, 10, 10";
      fields.threshold.value = "0.15";
      fields.minimum.value = "30";
      fields.input.checked = fields.output.checked = true;
      message.textContent = "Synthetic demonstration only: Add to pending changes, turn on Field comparisons for Input, and Save checks. Do not treat the fabricated reference as production evidence.";
    });
    run.addEventListener("click", () => {
      if (!original.some(item => item.rule_id === "ui_psi_demo")) {
        message.textContent = "Save the synthetic PSI sample on this tenant first, then load its test batch."; return;
      }
      document.getElementById("input-format").value = "json";
      document.getElementById("input-format").dispatchEvent(new Event("input", {bubbles: true}));
      document.getElementById("input-content").value = JSON.stringify({
        income_samples: [5, 15, ...Array(28).fill(25)]
      }, null, 2);
      document.getElementById("input-content").dispatchEvent(new Event("input", {bubbles: true}));
      message.textContent = "Synthetic shifted JSON batch loaded. Run Input checks; the saved tenant stage/action still determines the outcome.";
    });
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!form.reportValidity()) return;
      const id = fields.id.value;
      const pointer = fields.pointer.value;
      const selected = ["input", "output"].filter(stage => fields[stage].checked);
      const edges = decode(fields.edges.value);
      const countStrings = decode(fields.counts.value);
      const counts = countStrings.map(Number);
      const numbers = edges.map(Number);
      const threshold = Number(fields.threshold.value);
      const minimum = Number(fields.minimum.value);
      let error = "";
      if (!selected.length) error = "Choose Input, Output, or both.";
      else if (!pointer.startsWith("/") || /~(?![01])/.test(pointer) ||
          pointer.slice(1).split("/").some(x => !x)) error = "Use a valid absolute JSON pointer such as /income_samples.";
      else if (edges.length < 3 || edges.length > 65 ||
          numbers.some(v => !Number.isFinite(v)) ||
          numbers.some((v, i) => i > 0 && v <= numbers[i-1])) error = "Provide 3–65 strictly increasing finite bin edges.";
      else if (counts.length !== edges.length - 1 || counts.length < 2 || counts.length > 64 ||
          countStrings.some(v => !/^\d+$/.test(v)) ||
          counts.some(v => !Number.isSafeInteger(v) || v < 0)) error = "Provide one nonnegative integer count per bin.";
      else if (!Number.isInteger(minimum) || minimum < 30 || minimum > 100000 ||
          counts.reduce((a,b) => a+b, 0) < minimum) error = "Minimum must be 30–100000 and reference counts must meet it.";
      else if (!Number.isFinite(threshold) || threshold <= 0 || threshold > 10) error = "PSI threshold must be positive and no greater than 10.";
      else if (draft.some(v => v.rule_id === id && v.rule_id !== editing) ||
          (editing !== id && (tenant.record_rules || []).some(v => v.rule_id === "business." + id))) {
        error = "That rule ID is already in use on the tenant.";
      }
      if (error) {message.textContent = error; return;}
      // Preserve Decimal text for the existing strict server validator.
      const item = {rule_id:id, observations_pointer:pointer,
        reference_id: fields["reference-id"].value, reference_version: fields["reference-version"].value,
        bin_edges:edges, counts, psi_threshold:fields.threshold.value,
        min_observations: minimum, stages:selected};
      if (editing !== null) draft = draft.map(v => v.rule_id === editing ? item : v);
      else draft.push(item);
      clear(); draw(); changed();
      message.textContent = "PSI batch rule pending. Save checks to apply; the tenant stage/action remains authoritative.";
    });
    draw();
  }
  window.statisticalDriftEditor = {render, delta, actions, affectsStage};
})();
