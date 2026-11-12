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
      return "Saved identity rules: " + original.length + ". No pending changes. Protected references unchanged.";
    const {added, updated, removed} = pendingImpact();
    return "Unsaved changes — " + added + " added, " + updated + " updated, " +
      removed + " removed." + (added + updated + removed === 0 ? " Rule order changed." : "") +
      " Save checks to apply editable rules; protected references remain unchanged.";
  }
  function delta() {
    if (!enabled || samePortfolio(original, draft)) return null;
    // Scoped replacement leaves server-owned reference rules untouched.
    return protectedRules.length ? {editable: clone(draft)} : clone(draft);
  }
  function actions(stage, previous) {
    const result = {...previous};
    if (!enabled) return result;
    // One pass over the editable draft; protected host-owned IDs never enter
    // this cleanup set, and unrelated per-rule actions remain unchanged.
    const active = new Set(draft.filter(rule => rule.stages.includes(stage)).map(rule => rule.rule_id));
    for (const item of original) {
      if (!active.has(item.rule_id)) delete result["business." + item.rule_id];
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
    host.append(el("p", "Configure exact order-ID equality and unique serial-number checks for JSON. Each saved rule uses the same tenant cross_field Engine and Input/Output rule actions. Nested object and array paths (for example /shipments/0/order_id) are supported within strict JSON limits. Trusted quarantine/scope reference sets are never exposed or editable here. For a configured protected scope-subset rule, an explicit empty requested-scopes array means no permissions requested and passes; missing, null, malformed, oversized, or unauthorized scopes still produce a finding. Run a JSON Input/Output check to test the saved rule."));
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
      references.append(el("p", "Protected identity references remain read-only. Ordinary identity rules can still be edited; Save checks submits only public definitions and the server preserves hidden trusted sets under the tenant revision. Use authorized reference management for protected rules."));
    }
    host.append(references);
    const locked = false; // Protected reference rows are never in the editable draft.
    // Display-only search of editable tenant definitions; protected operator-owned
    // references are outside this draft and never searched or exposed.
    const filterBar = el("div");
    const searchLabel = el("label", "Find identity rule by ID, type or JSON pointer");
    searchLabel.htmlFor = "identity-filter";
    const searchInput = el("input"); searchInput.id = "identity-filter";
    searchInput.type = "search"; searchInput.maxLength = 160; searchInput.autocomplete = "off";
    const stageLabel = el("label", "Show identity rules on stage");
    stageLabel.htmlFor = "identity-filter-stage";
    const stageFilter = el("select"); stageFilter.id = "identity-filter-stage";
    for (const [value, caption] of [["all", "All stages"], ["input", "Input"], ["output", "Output"]]) {
      const option = el("option", caption); option.value = value; stageFilter.append(option);
    }
    stageFilter.value = "all";
    filterBar.append(searchLabel, searchInput, stageLabel, stageFilter);
    const filterStatus = el("p"); filterStatus.id = "identity-filter-status";
    filterStatus.setAttribute("role", "status"); filterStatus.setAttribute("aria-live", "polite");
    const preview = el("p"); preview.id = "identity-draft-summary";
    preview.setAttribute("role", "status"); preview.setAttribute("aria-live", "polite");
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
    // A guided example is only a form fill, not a tenant change or a new
    // evaluator. Customers must explicitly Add and then Save checks.
    const sampleHelp = el("p",
      "Order/shipment example: order A100 and shipment B200 violate ID equality; " +
      "matching A100 values satisfy this rule. Other tenant checks still apply.");
    sampleHelp.id = "identity-sample-help";
    const sample = el("button", "Fill example: shipment order ID must match order");
    sample.id = "identity-sample"; sample.type = "button";
    sample.addEventListener("click", () => {
      const id = "shipment_order_matches";
      // Refuse collisions, including deleted-but-unsaved, cross-family
      // drafts and protected reference-backed IDs. Never edit a saved rule.
      if (original.some(rule => rule.rule_id === id) ||
          draft.some(rule => rule.rule_id === id) ||
          (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_identity"))) {
        message.textContent = "Example rule ID already exists. Edit the existing rule or choose a different ID.";
        return;
      }
      clear();
      controls.id.value = id;
      controls.kind.value = "order_id_match";
      drawFields({order_id: "/order/id", shipment_order_id: "/shipment/order_id"});
      controls.input.checked = controls.output.checked = true;
      message.textContent = "Example filled, not added or saved. Choose Add pending identity rule, " +
        "then Save checks. Enable Field comparisons with Block on each stage to stop violations. " +
        "Sample JSON: {\"order\":{\"id\":\"A100\"},\"shipment\":{\"order_id\":\"B200\"}}.";
      controls.id.focus();
    });
    const serialHelp = el("p",
      "Serial uniqueness example: two identical serial IDs in a shipment violate " +
      "the rule; distinct values satisfy it. This checks only the submitted array, " +
      "not an external manufacturer registry.");
    serialHelp.id = "identity-serial-help";
    const serialSample = el("button", "Fill example: shipment serial numbers must be unique");
    serialSample.id = "identity-sample-serial"; serialSample.type = "button";
    serialSample.addEventListener("click", () => {
      const id = "shipment_serials_unique";
      if (original.some(rule => rule.rule_id === id) ||
          draft.some(rule => rule.rule_id === id) ||
          (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_identity"))) {
        message.textContent = "Example rule ID already exists. Edit the existing rule or choose a different ID.";
        return;
      }
      clear();
      controls.id.value = id;
      controls.kind.value = "serial_unique";
      drawFields({serials: "/shipment/serials"});
      controls.input.checked = controls.output.checked = true;
      message.textContent = "Example filled, not added or saved. Choose Add pending identity rule, " +
        "then Save checks. Enable Field comparisons with Block on both stages. " +
        'Sample JSON: {"shipment":{"serials":["SN100","SN100"]}}.';
      controls.id.focus();
    });
    form.append(sampleHelp, sample, serialHelp, serialSample, apply, cancel, message);
    host.append(filterBar, filterStatus, preview, list, form);
    // Bulk authoring edits ONLY public identity rules. Trusted quarantine/scope
    // references are host-owned and never included in the JSON draft.
    const bulk = el("details"); bulk.id = "identity-bulk-panel";
    bulk.append(el("summary", "Bulk JSON import / export public identity rules"));
    bulk.append(el("p", "Import at most 500 public order-ID and serial-uniqueness definitions as a JSON array (rule_id, kind, fields, stages). An empty trusted_values array is allowed for compatibility; nonempty values and protected reference kinds are refused. Import replaces ALL unsaved editable identity rows, including filtered ones. Save checks is required to persist."));
    const bulkLabel = el("label", "Editable public identity definitions as JSON array");
    bulkLabel.htmlFor = "identity-bulk-json";
    const bulkJson = el("textarea"); bulkJson.id = "identity-bulk-json";
    bulkJson.rows = 7; bulkJson.maxLength = 512000;
    bulkJson.setAttribute("spellcheck", "false");
    // A fill-only sample makes both public identity kinds discoverable without
    // editing a customer draft, tenant record, or protected reference set.
    const sampleJson = el("button", "Fill example JSON: order match and serial uniqueness");
    sampleJson.id = "identity-bulk-sample"; sampleJson.type = "button";
    const exportDraft = el("button", "Load editable identity draft into JSON box");
    exportDraft.id = "identity-bulk-export"; exportDraft.type = "button";
    const acknowledgeLabel = el("label");
    const acknowledge = el("input"); acknowledge.type = "checkbox";
    acknowledge.id = "identity-bulk-confirm";
    acknowledgeLabel.append(acknowledge, document.createTextNode(
      " I understand this replaces the entire unsaved editable identity draft, not only filtered rules. Protected reference-backed rules are unchanged. Save checks is still required."));
    const importDraft = el("button", "Stage identity JSON replacement");
    importDraft.id = "identity-bulk-import"; importDraft.type = "button";
    const bulkStatus = el("p"); bulkStatus.id = "identity-bulk-status";
    bulkStatus.setAttribute("role", "status"); bulkStatus.setAttribute("aria-live", "polite");
    bulk.append(bulkLabel, bulkJson, sampleJson, exportDraft, acknowledgeLabel, importDraft, bulkStatus);
    host.insertBefore(bulk, form);
    sampleJson.addEventListener("click", () => {
      bulkJson.value = JSON.stringify([
        {rule_id:"example_order_id_match", kind:"order_id_match",
          fields:{order_id:"/order/id", shipment_order_id:"/shipment/order_id"},
          stages:["input","output"]},
        {rule_id:"example_serial_unique", kind:"serial_unique",
          fields:{serials:"/shipment/serials"}, stages:["input","output"]}
      ], null, 2);
      acknowledge.checked = false;
      bulkStatus.textContent = "Two public identity examples loaded into the JSON box only. " +
        "No draft or tenant change. Review, confirm full editable draft replacement, " +
        "stage, choose Input/Output actions, then Save checks. Protected references unchanged.";
      bulkJson.focus();
    });
    exportDraft.addEventListener("click", () => {
      bulkJson.value = JSON.stringify(draft, null, 2);
      acknowledge.checked = false;
      bulkStatus.textContent = draft.length + " editable identity rules exported; protected references excluded. Not saved.";
    });
    function importRules(raw) {
      if (!Array.isArray(raw)) throw Error("Use a JSON array of public identity rules.");
      if (raw.length > 500) throw Error("At most 500 identity rules may be staged.");
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
        "conversionEditor"]) {
        const editor = window[name];
        const pending = editor && typeof editor.delta === "function" ? editor.delta() : null;
        const rules = Array.isArray(pending) ? pending :
          pending && Array.isArray(pending.editable) ? pending.editable : [];
        for (const item of rules) {
          if (item && typeof item.rule_id === "string") pendingOtherIds.add(item.rule_id);
        }
      }
      const decodedPath = value => value.slice(1).split("/").map(part =>
        part.replaceAll("~1", "/").replaceAll("~0", "~"));
      return raw.map((rule, index) => {
        const fail = reason => { throw Error("Identity rule " + (index + 1) + ": " + reason); };
        if (!rule || typeof rule !== "object" || Array.isArray(rule))
          return fail("expected an object.");
        if (Object.keys(rule).some(key => ![
          "rule_id", "kind", "fields", "stages", "trusted_values"
        ].includes(key))) return fail("unknown or protected rule field.");
        const id = rule.rule_id;
        if (typeof id !== "string" || !/^[a-z][a-z0-9_]{0,79}$/.test(id))
          return fail("invalid rule_id (lowercase, up to 80 characters).");
        if (ids.has(id) || (!savedIds.has(id) && reserved.has("business." + id)) ||
            (protectedIds.has(id) || protectedIds.has("business." + id)) ||
            pendingOtherIds.has(id))
          return fail("duplicate or reserved rule_id, including protected references or other family drafts.");
        if (!Object.prototype.hasOwnProperty.call(TYPES, rule.kind))
          return fail("unknown or protected identity rule kind.");
        if (Object.prototype.hasOwnProperty.call(rule, "trusted_values") &&
            (!Array.isArray(rule.trusted_values) || rule.trusted_values.length))
          return fail("trusted_values must be an empty array; private reference data cannot be imported.");
        if (!rule.fields || typeof rule.fields !== "object" || Array.isArray(rule.fields) ||
            Object.keys(rule.fields).length !== TYPES[rule.kind].length ||
            TYPES[rule.kind].some(key => !Object.prototype.hasOwnProperty.call(rule.fields, key)))
          return fail("fields must exactly match the public identity rule kind.");
        const fields = {}, seen = [];
        for (const key of TYPES[rule.kind]) {
          const pointer = rule.fields[key];
          if (typeof pointer !== "string" || pointer.length > 200 ||
              !pointer.startsWith("/") || /~(?![01])/.test(pointer))
            return fail("fields require valid absolute JSON pointers of at most 200 characters.");
          const segments = decodedPath(pointer);
          if (segments.some(part => !part) ||
              seen.some(previous => previous.every((part, i) => part === segments[i]) ||
                segments.every((part, i) => part === previous[i])))
            return fail("field pointers must have nonempty segments and be distinct and nonoverlapping.");
          seen.push(segments);
          fields[key] = pointer;
        }
        const stages = rule.stages;
        if (!Array.isArray(stages) || !stages.length || stages.length > 2 ||
            new Set(stages).size !== stages.length ||
            stages.some(stage => stage !== "input" && stage !== "output"))
          return fail("stages must contain Input, Output or both without duplicates.");
        ids.add(id);
        return {rule_id:id, kind:rule.kind, fields, trusted_values:[], stages};
      });
    }
    importDraft.addEventListener("click", () => {
      if (!acknowledge.checked) {
        bulkStatus.textContent = "Confirm replacement of the entire editable identity draft first.";
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
          " public identity rules staged. Protected reference-backed rules unchanged. Review Input/Output and select Save checks; no server change yet.";
      } catch (error) {
        // No customer JSON, pointers or private values in the failure message.
        bulkStatus.textContent = error instanceof SyntaxError
          ? "Invalid JSON; provide an array of public identity rule objects."
          : error.message;
      }
    });
    searchInput.addEventListener("input", draw);
    stageFilter.addEventListener("change", draw);
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
      preview.textContent = previewText();
      const query = searchInput.value.trim().toLowerCase();
      const stage = stageFilter.value || "all";
      const visible = draft.filter(rule =>
        (stage === "all" || rule.stages.includes(stage)) &&
        (!query || rule.rule_id.toLowerCase().includes(query) ||
          rule.kind.toLowerCase().includes(query) ||
          Object.values(rule.fields || {}).some(pointer =>
            typeof pointer === "string" && pointer.toLowerCase().includes(query))));
      filterStatus.textContent = "Showing " + visible.length + " of " + draft.length + " editable identity rules.";
      if (!visible.length) list.append(el("li", draft.length ?
        "No matching identity rules. Clear the search or stage filter." :
        "No editable identity rules on this tenant."));
      for (const rule of visible) {
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
        const persisted = original.find(x => x.rule_id === rule.rule_id);
        const sameDefinition = persisted &&
          persisted.kind === rule.kind &&
          Object.keys(persisted.fields).length === Object.keys(rule.fields).length &&
          Object.entries(persisted.fields).every(([key, value]) => rule.fields[key] === value) &&
          persisted.stages.length === rule.stages.length &&
          persisted.stages.every(stage => rule.stages.includes(stage));
        if (persisted && !sameDefinition) {
          item.append(el("p", "Unsaved identity rule changes: Save checks before using refreshed examples."));
        }
        const example = (tenant.record_rules || []).find(x => x.rule_id === "business." + rule.rule_id);
        if (sameDefinition) {
          if (!example) {
            item.append(el("p", "No record-generated examples are available for this saved rule. Stage settings still apply; test an appropriate JSON document using the shared tenant Engine. Private reference values remain hidden."));
          } else if (example.example_unavailable || example.look_alike_unavailable) {
            item.append(el("p", "Some synthetic examples are unavailable under the strict JSON admission or selected rule contract. The saved definition remains subject to stage check and rule actions; use shallower JSON pointers to restore examples when permitted. Private reference values are never shown."));
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
            for (const [label, sample] of [["violating", example?.example],
                                           ["benign", example?.look_alike]]) {
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
        message.textContent = "Protected references cannot be edited in this public rule editor.";
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
          (editing === null && exists && !original.some(x => x.rule_id === id)) ||
          (window.comparisonEditor &&
           window.comparisonEditor.otherFamilyDraftHasId(id, "advanced_identity"))) {
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
    for (const input of form.querySelectorAll("input, select, button")) input.disabled = false;
    draw();
  }
  window.identityEditor = {render, delta, actions};
})();
