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
  // Canonicalize field-object key order and Input/Output stage order only.
  // The order of RULES and all non-stage arrays remains meaningful.
  // This prevents no-op customer edits from incrementing the tenant revision.
  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value !== null && typeof value === "object") {
      // The API serializes nullable, non-FX conversion fields as null while
      // the customer form omits those same unset defaults. Both represent the
      // identical tenant policy; do not manufacture a pending change. Keep any
      // real non-null reference/return-event values significant.
      const nullable = new Set([
        "return_event_pointer", "source_currency", "destination_currency",
        "trusted_rate_numerator", "trusted_rate_denominator",
        "rate_reference", "rate_asof"
      ]);
      return Object.fromEntries(Object.keys(value)
        .filter(key => !(nullable.has(key) && value[key] === null))
        .sort().map(key => [
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
  // Count every editable tenant rule, including rules hidden by a search filter.
  // Protected host-owned reference values never enter this draft or preview.
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
      return "Saved conversion/time rules: " + original.length + ". No pending changes. Protected references unchanged.";
    const {added, updated, removed} = pendingImpact();
    return "Unsaved changes — " + added + " added, " + updated + " updated, " +
      removed + " removed." + (added + updated + removed === 0 ? " Rule order changed." : "") +
      " Save checks to apply editable rules; protected references remain unchanged.";
  }
  function delta() {
    if (!enabled || samePortfolio(original, draft)) return null;
    // Scoped replacement leaves server-owned FX rules untouched.
    return protectedRules.length ? {editable: clone(draft)} : clone(draft);
  }
  function actions(stage, previous) {
    const result = {...previous};
    if (!enabled) return result;
    // Keep stage-action reconciliation O(saved + draft) at 500-rule scale;
    // never touch other comparison families or protected FX references.
    const active = new Set(draft.filter(rule => rule.stages.includes(stage)).map(rule => rule.rule_id));
    for (const item of original) {
      if (!active.has(item.rule_id)) delete result["business." + item.rule_id];
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
    // Display-only search of editable tenant definitions; protected operator-owned
    // references are outside this draft and never searched or exposed.
    const filterBar = el("div");
    const searchLabel = el("label", "Find conversion rule by ID, type or JSON pointer");
    searchLabel.htmlFor = "conversion-filter";
    const searchInput = el("input"); searchInput.id = "conversion-filter";
    searchInput.type = "search"; searchInput.maxLength = 160; searchInput.autocomplete = "off";
    const stageLabel = el("label", "Show conversion rules on stage");
    stageLabel.htmlFor = "conversion-filter-stage";
    const stageFilter = el("select"); stageFilter.id = "conversion-filter-stage";
    for (const [value, caption] of [["all", "All stages"], ["input", "Input"], ["output", "Output"]]) {
      const option = el("option", caption); option.value = value; stageFilter.append(option);
    }
    stageFilter.value = "all";
    filterBar.append(searchLabel, searchInput, stageLabel, stageFilter);
    const filterStatus = el("p"); filterStatus.id = "conversion-filter-status";
    filterStatus.setAttribute("role", "status"); filterStatus.setAttribute("aria-live", "polite");
    const preview = el("p"); preview.id = "conversion-draft-summary";
    preview.setAttribute("role", "status"); preview.setAttribute("aria-live", "polite");
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
    // Guided deterministic sample: only fill the existing customer form.
    // The server-owned tenant definition changes only after Add and Save.
    const sampleHelp = el("p",
      "Cargo-load example: 1500 kg exceeds the declared 1000 kg limit; " +
      "1000 kg is valid for this rule. Other enabled tenant checks still run.");
    sampleHelp.id = "conversion-sample-help";
    const sample = el("button", "Fill example: cargo load must not exceed kg limit");
    sample.id = "conversion-sample"; sample.type = "button";
    sample.addEventListener("click", () => {
      const id = "cargo_mass_within_limit";
      // Saved, removed-but-unsaved, other-family and host-protected ID
      // collisions are not permission to overwrite any existing rule.
      if (original.some(rule => rule.rule_id === id) ||
          draft.some(rule => rule.rule_id === id) ||
          (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_conversion_time"))) {
        message.textContent = "Example rule ID already exists. Edit the existing rule or choose a different ID.";
        return;
      }
      clear();
      controls.id.value = id;
      controls.kind.value = "unit_load_kg_cap";
      drawFields({load: "/shipment/load", load_unit: "/shipment/load_unit",
                  max_kg: "/shipment/max_kg"});
      eventPointer.value = "";
      controls.input.checked = controls.output.checked = true;
      message.textContent = "Example filled, not added or saved. Choose Add pending conversion rule, " +
        "then Save checks. Enable Field comparisons with Block on Input/Output. " +
        "Sample JSON: {\"shipment\":{\"load\":1500,\"load_unit\":\"kg\",\"max_kg\":1000}}.";
      controls.id.focus();
    });
    const coldHelp = el("p",
      "Cold-chain example: excursion from 10:00 to 10:45 exceeds a declared " +
      "30-minute allowance; an end time of 10:20 satisfies the rule. " +
      "Only supplied timestamps are checked, not sensor authenticity.");
    coldHelp.id = "conversion-cold-chain-help";
    const coldSample = el("button", "Fill example: cold-chain excursion time limit");
    coldSample.id = "conversion-sample-cold-chain"; coldSample.type = "button";
    coldSample.addEventListener("click", () => {
      const id = "cold_chain_duration_within_limit";
      if (original.some(rule => rule.rule_id === id) ||
          draft.some(rule => rule.rule_id === id) ||
          (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_conversion_time"))) {
        message.textContent = "Example rule ID already exists. Edit the existing rule or choose a different ID.";
        return;
      }
      clear();
      controls.id.value = id;
      controls.kind.value = "cold_chain_excursion";
      drawFields({excursion_start: "/shipment/excursion/start",
                  excursion_end: "/shipment/excursion/end",
                  allowed_minutes: "/shipment/excursion/allowed_minutes"});
      controls.input.checked = controls.output.checked = true;
      message.textContent = "Example filled, not added or saved. Choose Add pending conversion rule, " +
        "then Save checks. Enable Field comparisons and select Block for Input/Output. " +
        'Example JSON: {"shipment":{"excursion":{"start":"2026-01-01T10:00:00Z",' +
        '"end":"2026-01-01T10:45:00Z","allowed_minutes":30}}}.';
      controls.id.focus();
    });

    const lifecycleHelp = el("p",
      "Fulfillment lifecycle example: order 09:00, shipment 12:00 and delivery 11:00 " +
      "violate chronology. Order 09:00, shipment 10:00 and delivery 11:00 pass. " +
      "A return record is optional until recorded; a present incomplete return fails.");
    lifecycleHelp.id = "conversion-lifecycle-help";
    const lifecycleSample = el("button", "Fill example: order, shipment, delivery and return chronology");
    lifecycleSample.id = "conversion-sample-lifecycle"; lifecycleSample.type = "button";
    lifecycleSample.addEventListener("click", () => {
      const id = "fulfillment_lifecycle_ordered";
      if (original.some(rule => rule.rule_id === id) ||
          draft.some(rule => rule.rule_id === id) ||
          (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_conversion_time"))) {
        message.textContent = "Example rule ID already exists. Edit the existing rule or choose a different ID.";
        return;
      }
      clear();
      controls.id.value = id;
      controls.kind.value = "lifecycle_chronology";
      drawFields({order_at: "/fulfillment/order_at",
                  shipment_at: "/fulfillment/shipment_at",
                  delivery_at: "/fulfillment/delivery_at",
                  return_at: "/fulfillment/return/at"});
      eventPointer.value = "/fulfillment/return";
      controls.input.checked = controls.output.checked = true;
      message.textContent = "Example filled, not added or saved. Choose Add pending conversion rule, " +
        "then Save checks. Enable Field comparisons with Block on Input/Output. " +
        'Example JSON: {"fulfillment":{"order_at":"2026-01-01T09:00:00Z",' +
        '"shipment_at":"2026-01-01T12:00:00Z","delivery_at":"2026-01-01T11:00:00Z"}}. ' +
        "Return is optional until an event is recorded; a partial event must fail.";
      controls.id.focus();
    });
    form.append(sampleHelp, sample, coldHelp, coldSample, lifecycleHelp, lifecycleSample, apply, cancel, message);
    host.append(filterBar, filterStatus, preview, list, form);
    // Bulk staging is only an editable, unsaved public conversion draft:
    // existing operator-signed FX reference values never enter this control.
    const bulk = el("details"); bulk.id = "conversion-bulk-panel";
    bulk.append(el("summary", "Bulk JSON import / export conversion and time rules"));
    bulk.append(el("p", "Paste up to 500 public conversion/time definitions (rule_id, kind, fields, stages; optional return_event_pointer for lifecycle chronology). Staging replaces ALL unsaved editable conversion rules, even filtered rows. It cannot edit protected FX references, enable checks or persist the tenant until Save checks."));
    const bulkLabel = el("label", "Editable conversion/time rule definitions as JSON array");
    bulkLabel.htmlFor = "conversion-bulk-json";
    const bulkJson = el("textarea"); bulkJson.id = "conversion-bulk-json";
    bulkJson.rows = 7; bulkJson.maxLength = 512000;
    bulkJson.setAttribute("spellcheck", "false");
    // A single fill-only example lets customers preview two complementary
    // deterministic conversion/time checks before any draft or tenant mutation.
    const sampleJson = el("button", "Fill example JSON: cargo and cold-chain checks");
    sampleJson.id = "conversion-bulk-sample"; sampleJson.type = "button";
    sampleJson.addEventListener("click", () => {
      bulkJson.value = JSON.stringify([
        {rule_id:"example_cargo_kg_cap", kind:"unit_load_kg_cap",
          fields:{load:"/shipment/load",load_unit:"/shipment/load_unit",
            max_kg:"/shipment/max_kg"}, stages:["input","output"]},
        {rule_id:"example_cold_chain_duration", kind:"cold_chain_excursion",
          fields:{excursion_start:"/shipment/excursion/start",
            excursion_end:"/shipment/excursion/end",
            allowed_minutes:"/shipment/excursion/allowed_minutes"},
          stages:["input","output"]}
      ], null, 2);
      acknowledge.checked = false;
      bulkStatus.textContent = "Two cargo/time examples loaded into the JSON box only. " +
        "No draft or tenant change. Confirm, stage, then Save checks to activate.";
      bulkJson.focus();
    });
    const exportDraft = el("button", "Load editable conversion draft into JSON box");
    exportDraft.type = "button"; exportDraft.id = "conversion-bulk-export";
    const acknowledgeLabel = el("label");
    const acknowledge = el("input"); acknowledge.type = "checkbox";
    acknowledge.id = "conversion-bulk-confirm";
    acknowledgeLabel.append(acknowledge, document.createTextNode(
      " I understand this replaces the entire editable conversion draft, not protected FX references or just filtered rules. Save checks is still required."));
    const importDraft = el("button", "Stage conversion JSON replacement");
    importDraft.type = "button"; importDraft.id = "conversion-bulk-import";
    const bulkStatus = el("p"); bulkStatus.id = "conversion-bulk-status";
    bulkStatus.setAttribute("role", "status"); bulkStatus.setAttribute("aria-live", "polite");
    bulk.append(bulkLabel, bulkJson, sampleJson, exportDraft, acknowledgeLabel, importDraft, bulkStatus);
    host.insertBefore(bulk, form);
    // Whole-draft replacement consent applies to the exact reviewed JSON,
    // not later edits in a 500-rule import. Do not affect saved tenant state.
    bulkJson.addEventListener("input", () => {
      acknowledge.checked = false;
      bulkStatus.textContent = "JSON changed. Review and confirm the entire draft replacement again before staging; the tenant is unchanged.";
    });
    exportDraft.addEventListener("click", () => {
      bulkJson.value = JSON.stringify(draft, null, 2);
      acknowledge.checked = false;
      bulkStatus.textContent = draft.length + " editable conversion rules loaded into JSON box; protected FX references excluded. Not saved.";
    });
    function importRules(raw) {
      if (!Array.isArray(raw)) throw Error("Use a JSON array of conversion rule objects.");
      if (raw.length > 500) throw Error("At most 500 conversion rules may be staged.");
      const ids = new Set();
      const savedIds = new Set(original.map(rule => rule.rule_id));
      const reserved = new Set((tenant.record_rules || [])
        .filter(rule => rule && typeof rule.rule_id === "string")
        .map(rule => rule.rule_id));
      // Snapshot protected identifiers and other families' unsaved public drafts
      // once. An O(500 x other-editor delta) scan blocks customer bulk imports;
      // saved IDs remain independently reserved by the authoritative tenant.
      const protectedIds = new Set(protectedRules.flatMap(item =>
        [item.rule_id, item.rule_id.startsWith("business.")
          ? item.rule_id.slice("business.".length) : "business." + item.rule_id]));
      const pendingOtherIds = new Set();
      for (const name of ["comparisonEditor", "arithmeticEditor",
        "identityEditor"]) {
        const editor = window[name];
        const pending = editor && typeof editor.delta === "function" ? editor.delta() : null;
        const rules = Array.isArray(pending) ? pending :
          pending && Array.isArray(pending.editable) ? pending.editable : [];
        for (const item of rules) {
          if (item && typeof item.rule_id === "string") pendingOtherIds.add(item.rule_id);
        }
      }
      const pathSegments = value => value.slice(1).split("/").map(part =>
        part.replaceAll("~1", "/").replaceAll("~0", "~"));
      return raw.map((rule, index) => {
        const fail = reason => { throw Error("Conversion rule " + (index + 1) + ": " + reason); };
        if (!rule || typeof rule !== "object" || Array.isArray(rule))
          return fail("expected an object.");
        if (Object.keys(rule).some(key => ![
          "rule_id", "kind", "fields", "stages", "return_event_pointer"
        ].includes(key))) return fail("unknown or protected rule field.");
        const id = rule.rule_id;
        if (typeof id !== "string" || !/^[a-z][a-z0-9_]{0,79}$/.test(id))
          return fail("invalid rule_id (lowercase, up to 80 characters).");
        if (ids.has(id) || (!savedIds.has(id) && reserved.has("business." + id)) ||
            (protectedIds.has(id) || protectedIds.has("business." + id)) ||
            pendingOtherIds.has(id))
          return fail("duplicate or reserved rule_id, including protected references or other family drafts.");
        if (typeof rule.kind !== "string" ||
            !Object.prototype.hasOwnProperty.call(TYPES, rule.kind))
          return fail("unknown or protected conversion rule kind.");
        if (!rule.fields || typeof rule.fields !== "object" || Array.isArray(rule.fields) ||
            Object.keys(rule.fields).length !== TYPES[rule.kind].length ||
            TYPES[rule.kind].some(key => !Object.prototype.hasOwnProperty.call(rule.fields, key)))
          return fail("fields must exactly match the public conversion rule kind.");
        const fields = {}, decoded = [];
        for (const key of TYPES[rule.kind]) {
          const pointer = rule.fields[key];
          if (typeof pointer !== "string" || pointer.length > 200 ||
              !pointer.startsWith("/") || /~(?![01])/.test(pointer))
            return fail("fields require valid absolute JSON pointers of at most 200 characters.");
          const segments = pathSegments(pointer);
          if (segments.some(part => !part) ||
              decoded.some(previous => previous.every((part, i) => part === segments[i]) ||
                segments.every((part, i) => part === previous[i])))
            return fail("field pointers must have nonempty segments and be distinct and nonoverlapping.");
          decoded.push(segments); fields[key] = pointer;
        }
        const stages = rule.stages === undefined ? ["input", "output"] : rule.stages;
        if (!Array.isArray(stages) || !stages.length || stages.length > 2 ||
            new Set(stages).size !== stages.length ||
            stages.some(stage => stage !== "input" && stage !== "output"))
          return fail("stages must contain Input, Output or both without duplicates.");
        let returnEvent = null;
        if (Object.prototype.hasOwnProperty.call(rule, "return_event_pointer") &&
            rule.return_event_pointer !== null) {
          if (rule.kind !== "lifecycle_chronology")
            return fail("return_event_pointer belongs only to lifecycle chronology.");
          const pointer = rule.return_event_pointer;
          if (typeof pointer !== "string" || pointer.length > 200 ||
              !pointer.startsWith("/") || /~(?![01])/.test(pointer))
            return fail("return_event_pointer must be a valid absolute JSON pointer.");
          const event = pathSegments(pointer);
          const returnTime = pathSegments(fields.return_at);
          if (event.some(part => !part) || event.length >= returnTime.length ||
              !event.every((part, i) => part === returnTime[i]))
            return fail("return event must be a parent of the return timestamp.");
          if (["order_at", "shipment_at", "delivery_at"].some(key => {
            const mandatory = pathSegments(fields[key]);
            return event.length <= mandatory.length &&
              event.every((part, i) => part === mandatory[i]);
          })) return fail("return event cannot contain mandatory timestamps.");
          returnEvent = pointer;
        }
        ids.add(id);
        const item = {rule_id: id, kind: rule.kind, fields, stages};
        if (returnEvent !== null) item.return_event_pointer = returnEvent;
        return item;
      });
    }
    importDraft.addEventListener("click", () => {
      if (!acknowledge.checked) {
        bulkStatus.textContent = "Confirm replacement of the entire editable conversion draft first.";
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
          " conversion rules staged. Protected FX references unchanged. Review Input/Output and select Save checks; no server change yet.";
      } catch (error) {
        // Never echo imported customer pointers, content or protected values.
        bulkStatus.textContent = error instanceof SyntaxError
          ? "Invalid JSON; provide an array of public conversion rule objects."
          : error.message;
      }
    });
    searchInput.addEventListener("input", draw);
    stageFilter.addEventListener("change", draw);
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
      preview.textContent = previewText();
      const query = searchInput.value.trim().toLowerCase();
      const stage = stageFilter.value || "all";
      const visible = draft.filter(rule =>
        (stage === "all" || rule.stages.includes(stage)) &&
        (!query || rule.rule_id.toLowerCase().includes(query) ||
          rule.kind.toLowerCase().includes(query) ||
          Object.values(rule.fields || {}).some(pointer =>
            typeof pointer === "string" && pointer.toLowerCase().includes(query)) ||
          (typeof rule.return_event_pointer === "string" &&
            rule.return_event_pointer.toLowerCase().includes(query))));
      filterStatus.textContent = "Showing " + visible.length + " of " + draft.length + " editable conversion rules.";
      if (!visible.length) list.append(el("li", draft.length ?
        "No matching conversion rules. Clear the search or stage filter." :
        "No editable conversion/time rules on this tenant."));
      for (const rule of visible) {
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
        const persisted = original.find(x => x.rule_id === rule.rule_id);
        const sameDefinition = persisted &&
          persisted.kind === rule.kind &&
          Object.keys(persisted.fields).length === Object.keys(rule.fields).length &&
          Object.entries(persisted.fields).every(([key, value]) => rule.fields[key] === value) &&
          persisted.stages.length === rule.stages.length &&
          persisted.stages.every(stage => rule.stages.includes(stage)) &&
          (persisted.return_event_pointer || null) === (rule.return_event_pointer || null);
        if (persisted && !sameDefinition) {
          item.append(el("p", "Unsaved conversion/time rule changes: Save checks before using refreshed examples."));
        }
        const example = (tenant.record_rules || []).find(x => x.rule_id === "business." + rule.rule_id);
        if (sameDefinition) {
          if (!example) {
            item.append(el("p", "No record-generated examples are available for this saved rule. Stage settings still apply; test an appropriate JSON document using the shared tenant Engine. Private FX references remain hidden."));
          }
          for (const stage of rule.stages) {
            // The saved parent check governs whether ANY comparison runs.
            // A pending dashboard change is not yet tenant-authoritative.
            // Keep the independent own-rule Off message when the check runs.
            const savedChecks = tenant[stage + "_checks"] || {};
            if (!savedChecks.cross_field || savedChecks.cross_field === "off") {
              item.append(el("p", "Saved " + stage + " Field comparisons check is Off. This rule is not evaluated on " + stage + " regardless of its own rule action; examples still test other enabled tenant checks."));
            } else if (tenant[stage + "_rule_actions"]?.["business." + rule.rule_id] === "off") {
              item.append(el("p", "Saved " + stage + " rule action is Off. Examples remain available for full-policy testing, but this rule will not report a finding on " + stage + " until you enable and Save checks."));
            }
            // The Engine-certified no-return sample exists only when the
            // saved FC-050 layout truly allows the return record to be absent.
            const samples = [["violating", example?.example], ["benign", example?.look_alike]];
            if (rule.kind === "lifecycle_chronology") {
              samples.push(["no-return", example?.no_return_example]);
            }
            for (const [label, sample] of samples) {
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
          (editing === null && exists && !original.some(x => x.rule_id === id)) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_conversion_time"))) {
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
