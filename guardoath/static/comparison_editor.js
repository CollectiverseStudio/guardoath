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
  // Inspect only pending public rule IDs. Protected reference values never
  // enter another editor's draft or this shared tenant-wide name check.
  function otherFamilyDraftHasId(id, owner) {
    for (const [family, name] of [
      ["comparisons", "comparisonEditor"],
      ["advanced_arithmetic", "arithmeticEditor"],
      ["advanced_identity", "identityEditor"],
      ["advanced_conversion_time", "conversionEditor"]
    ]) {
      if (family === owner) continue;
      const editor = window[name];
      const pending = editor && typeof editor.delta === "function" ? editor.delta() : null;
      // Owner-provisioned identity/FX references have a scoped editable delta.
      const rules = Array.isArray(pending) ? pending :
        pending && Array.isArray(pending.editable) ? pending.editable : [];
      if (rules.some(rule => rule && rule.rule_id === id)) return true;
    }
    return false;
  }
  function actions(stage, current) {
    const result = {...current};
    // Building one stage membership set keeps revisioned action cleanup linear
    // even for 500+ tenant-owned comparisons; only saved rules are eligible
    // for removal, and unrelated actions are left untouched.
    const activeIds = new Set(draft.filter(rule => rule.stages.includes(stage))
      .map(rule => rule.rule_id));
    for (const item of original) {
      if (!activeIds.has(item.rule_id)) delete result["business." + item.rule_id];
    }
    return result;
  }
  // Graph analysis is advisory only. Rules still run through the tenant-owned
  // shared Engine. Per stage and type, a <= path that returns through a strict
  // edge is impossible; a non-strict cycle forces its vertices to equality.
  // Restrict reports here to 3+ distinct fields; same-pair contradictions
  // already have a separate warning. Iterative traversal stays bounded at scale.
  function multiFieldConflicts(rules, stage) {
    const key = pointer => JSON.stringify(pointer.slice(1).split("/").map(
      segment => segment.replace(/~1/g, "/").replace(/~0/g, "~")));
    const graphs = new Map();
    for (const rule of rules) {
      if (!rule.stages.includes(stage)) continue;
      let g = graphs.get(rule.value_type);
      if (!g) {
        g = {forward: new Map(), backward: new Map(), nonStrictForward: new Map(), nonStrictBackward: new Map(), edges: [], different: []};
        graphs.set(rule.value_type, g);
      }
      const a = key(rule.left), b = key(rule.right);
      if (a === b) continue; // handled by self-comparison warning
      for (const node of [a, b]) {
        if (!g.forward.has(node)) {
          g.forward.set(node, []); g.backward.set(node, []);
          g.nonStrictForward.set(node, []); g.nonStrictBackward.set(node, []);
        }
      }
      const edge = (from, to, strict) => {
        g.forward.get(from).push(to);
        g.backward.get(to).push(from);
        if (!strict) {
          g.nonStrictForward.get(from).push(to);
          g.nonStrictBackward.get(to).push(from);
        }
        g.edges.push({from, to, strict});
      };
      switch (rule.operator) {
        case "lt": edge(a, b, true); break;
        case "le": edge(a, b, false); break;
        case "gt": edge(b, a, true); break;
        case "ge": edge(b, a, false); break;
        case "eq": edge(a, b, false); edge(b, a, false); break;
        case "ne": g.different.push([a, b]); break;
      }
    }
    // A directed strict edge can make a component impossible WITHOUT forcing
    // any two fields to equal values. "!= contradicted by forced equality"
    // requires paths in BOTH directions made of non-strict <=/>=/= edges.
    // Compute full SCC for strict cycles and non-strict SCC for equality,
    // rather than claiming a != contradiction from a strict cycle alone.
    function components(forward, backward) {
      const visited = new Set(), finished = [];
      for (const start of forward.keys()) {
        if (visited.has(start)) continue;
        const stack = [{node: start, index: 0}];
        visited.add(start);
        while (stack.length) {
          const current = stack[stack.length - 1];
          const outgoing = forward.get(current.node);
          if (current.index === outgoing.length) {
            finished.push(current.node); stack.pop(); continue;
          }
          const next = outgoing[current.index++];
          if (!visited.has(next)) {
            visited.add(next); stack.push({node: next, index: 0});
          }
        }
      }
      const component = new Map(), sizes = [];
      for (let i = finished.length - 1; i >= 0; i--) {
        const start = finished[i];
        if (component.has(start)) continue;
        const id = sizes.length, stack = [start];
        component.set(start, id);
        let size = 0;
        while (stack.length) {
          const current = stack.pop();
          size++;
          for (const neighbor of backward.get(current)) {
            if (!component.has(neighbor)) {
              component.set(neighbor, id); stack.push(neighbor);
            }
          }
        }
        sizes.push(size);
      }
      return {component, sizes};
    }
    let orderCycles = 0, forcedEqualDifferences = 0;
    for (const g of graphs.values()) {
      const {component, sizes} = components(g.forward, g.backward);
      const {component: nonStrictComponent, sizes: nonStrictSizes} =
        components(g.nonStrictForward, g.nonStrictBackward);
      const strictCycles = new Set();
      for (const {from, to, strict} of g.edges) {
        const id = component.get(from);
        if (strict && id === component.get(to) && sizes[id] >= 3)
          strictCycles.add(id);
      }
      orderCycles += strictCycles.size;
      const forcedDifferentPairs = new Set();
      for (const [a, b] of g.different) {
        const id = nonStrictComponent.get(a);
        if (id === nonStrictComponent.get(b) && nonStrictSizes[id] >= 3) {
          const pair = a < b ? [a, b] : [b, a];
          forcedDifferentPairs.add(JSON.stringify([id, ...pair]));
        }
      }
      forcedEqualDifferences += forcedDifferentPairs.size;
    }
    return {orderCycles, forcedEqualDifferences};
  }
  function render(host, tenant, enabled, changed) {
    original = Array.isArray(tenant.comparisons) ? copy(tenant.comparisons) : [];
    // Saved rule definitions are immutable for this render; indexing avoids
    // quadratic lookup while filtering or editing a large pending portfolio.
    const savedById = new Map(original.map(rule => [rule.rule_id, rule]));
    draft = copy(original); editing = null;
    available = enabled && Array.isArray(tenant.comparisons);
    host.replaceChildren(); host.hidden = !enabled;
    if (!enabled) return;
    host.append(element("h3", "Field comparison rules"));
    const note = element("p", "Rule examples demonstrate the selected comparison only. Running them evaluates ALL enabled tenant rules on that stage; other findings or schema errors may also appear. These examples do not change tenant settings.");
    note.dataset.fieldExampleScope = "ordinary"; host.append(note);
    host.append(element("p", "Create, edit or remove number and calendar-date comparisons on your record. Apply a draft here, then Save checks. A rule runs only on its selected stages where Field comparisons is on. If no rules remain on a stage, turn that check off there before saving."));
    if (!available) {
      host.append(element("p", "Comparison definitions could not be loaded. Reconnect before editing."));
      return;
    }
    const summary = element("p"); summary.id = "comparison-draft-summary"; summary.setAttribute("role", "status"); summary.setAttribute("aria-live", "polite");
    const limitWarning = element("p"); limitWarning.id = "comparison-structure-warning";
    limitWarning.setAttribute("role", "status"); limitWarning.setAttribute("aria-live", "polite");
    limitWarning.hidden = true;
    const logicWarning = element("p"); logicWarning.id = "comparison-logic-warning";
    logicWarning.setAttribute("role", "status"); logicWarning.setAttribute("aria-live", "polite");
    logicWarning.hidden = true;
    // Filtering is presentation-only; never narrow the authoritative draft,
    // exported JSON, revisioned Save, Engine checks or structural warnings.
    const filterBar = element("div");
    const filterLabel = element("label", "Find ordinary comparison by ID, JSON pointer, operator or value type");
    filterLabel.htmlFor = "comparison-filter";
    const filterInput = element("input"); filterInput.id = "comparison-filter";
    filterInput.type = "search"; filterInput.maxLength = 160;
    filterInput.autocomplete = "off";
    const stageLabel = element("label", "Show ordinary comparisons on stage");
    stageLabel.htmlFor = "comparison-filter-stage";
    const stageFilter = element("select"); stageFilter.id = "comparison-filter-stage";
    for (const [value, name] of [["all", "All stages"], ["input", "Input"], ["output", "Output"]]) {
      const option = element("option", name); option.value = value; stageFilter.append(option);
    }
    stageFilter.value = "all"; // stable default before and after dashboard reconnect
    filterBar.append(filterLabel, filterInput, stageLabel, stageFilter);
    const filterStatus = element("p"); filterStatus.id = "comparison-filter-status";
    filterStatus.setAttribute("role", "status"); filterStatus.setAttribute("aria-live", "polite");
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
    controls.id.pattern = "[a-z][a-z0-9_]{0,79}";
    form.append(element("p", "Comparison IDs must start with a lowercase letter and use only lowercase letters, digits and underscores (1–80 characters). This matches the tenant rule IDs accepted by the API."));
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
    form.append(apply, cancel, message); host.append(summary, limitWarning, logicWarning, filterBar, filterStatus, list, form);
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
    // A draft in another FC family has not entered tenant.record_rules yet.
    // Reject its ID before replacing hundreds of ordinary rules; the shared
    // Save-time namespace check remains the authoritative final guard.
    function otherPendingIds() {
      const ids = new Set();
      for (const name of ["arithmeticEditor", "identityEditor", "conversionEditor"]) {
        const editor = window[name];
        const pending = editor && typeof editor.delta === "function" ? editor.delta() : null;
        // Protected identity/FX reference sets use {editable: [...]} so
        // ordinary edits cannot silently collide with those scoped drafts.
        const rules = Array.isArray(pending) ? pending :
          pending && Array.isArray(pending.editable) ? pending.editable : [];
        for (const rule of rules) {
          if (rule && typeof rule.rule_id === "string") ids.add(rule.rule_id);
        }
      }
      return ids;
    }
    function importRules(raw) {
      if (!Array.isArray(raw)) throw Error("Use a JSON array of comparison objects.");
      const allowed = new Set(["rule_id", "left", "right", "operator", "value_type", "stages"]);
      const operators = new Set(["lt", "le", "eq", "ge", "gt", "ne"]);
      const ids = new Set();
      const existing = new Set(original.map(rule => rule.rule_id));
      const pendingAdvancedIds = otherPendingIds();
      const otherIds = new Set((tenant.record_rules || [])
        .filter(rule => rule && typeof rule.rule_id === "string")
        .map(rule => rule.rule_id));
      return raw.map((rule, i) => {
        const fail = reason => { throw Error("Comparison " + (i + 1) + ": " + reason); };
        if (!rule || typeof rule !== "object" || Array.isArray(rule)) return fail("expected an object.");
        if (Object.keys(rule).some(key => !allowed.has(key))) return fail("unknown field; use only rule_id, left, right, operator, value_type and stages.");
        const id = rule.rule_id;
        if (typeof id !== "string" || !/^[a-z][a-z0-9_]{0,79}$/.test(id)) return fail("invalid rule_id; use a lowercase letter followed by lowercase letters, digits or underscores (1–80 characters).");
        if (ids.has(id) || pendingAdvancedIds.has(id) ||
            (!existing.has(id) && otherIds.has("business." + id)))
          return fail("duplicate or reserved rule_id, including pending advanced-family changes.");
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
    function drawRows() {
      list.replaceChildren();
      const query = filterInput.value.trim().toLowerCase();
      const stage = stageFilter.value;
      const shown = draft.filter(item =>
        (stage === "all" || item.stages.includes(stage)) &&
        (!query || [item.rule_id, item.left, item.right, item.operator, item.value_type].some(value => value.toLowerCase().includes(query))));
      filterStatus.textContent = "Showing " + shown.length + " of " + draft.length +
        " ordinary comparisons. Filtering does not change the draft, saved tenant or executed rules.";
      if (!draft.length) list.append(element("li", "No number or date comparisons on this record."));
      else if (!shown.length) list.append(element("li", "No matching ordinary comparisons. Clear the filter to see every rule."));
      for (const item of shown) {
        const row = element("li"); row.dataset.comparisonId = item.rule_id;
        row.append(element("span", item.rule_id + ": " + item.left + " " + item.operator + " " + item.right + " (" + item.value_type + "; " + item.stages.join(", ") + ") "));
        // The ordinary editor must expose the same saved per-stage Off state
        // as the advanced-family editors. A pending definition is not yet the
        // tenant's authoritative saved rule, so do not label it as such.
        const persisted = savedById.get(item.rule_id);
        const savedDefinition = persisted &&
          persisted.left === item.left && persisted.right === item.right &&
          persisted.operator === item.operator && persisted.value_type === item.value_type &&
          persisted.stages.length === item.stages.length &&
          persisted.stages.every(stage => item.stages.includes(stage));
        if (persisted && !savedDefinition) {
          row.append(element("p", "Unsaved comparison changes: Save checks before testing the updated rule."));
        } else if (savedDefinition) {
          for (const stage of item.stages) {
            // Stage-wide Off takes precedence over this rule's own action:
            // a saved per-rule Block cannot run while its parent check is Off.
            // Read the saved tenant record, never the mutable dashboard draft.
            const stageChecks = tenant[stage + "_checks"] || {};
            if (!stageChecks.cross_field || stageChecks.cross_field === "off") {
              row.append(element("p", "Saved " + stage + " Field comparisons check is Off. This rule is not evaluated on " + stage + " regardless of its own rule action; testing examples still runs other enabled checks."));
            } else if (tenant[stage + "_rule_actions"]?.["business." + item.rule_id] === "off") {
              row.append(element("p", "Saved " + stage + " rule action is Off. This rule is not evaluated on " + stage + "; testing examples still runs all other enabled tenant rules."));
            }
          }
        }
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
    filterInput.addEventListener("input", drawRows);
    stageFilter.addEventListener("change", drawRows);
    function draw() {
      const pending = JSON.stringify(original) !== JSON.stringify(draft);
      summary.textContent = `${draft.length} comparison${draft.length === 1 ? "" : "s"} configured; ${draft.filter(rule => rule.stages.includes("input")).length} Input, ${draft.filter(rule => rule.stages.includes("output")).length} Output.${pending ? " Unsaved changes — select Save checks to persist." : " No pending changes."}`;
      // The shared JSON decoder limits *every* object to 1,000 members,
      // not just the root. Count distinct children at each pointer prefix in
      // each stage. A pair of sibling maps with 501 fields each is valid;
      // 1,002 fields under one nested map cannot pass when fully populated.
      // This is advisory and never imposes a tenant comparison-count cap.
      const warnings = [];
      const logicWarnings = [];
      for (const stage of ["input", "output"]) {
        const scopes = new Map(); // JSON-pointer prefix -> unique direct children
        let deepest = 0; // scalar at /a/b/c requires three nested JSON containers
        for (const rule of draft) {
          if (!rule.stages.includes(stage)) continue;
          for (const pointer of [rule.left, rule.right]) {
            const parts = pointer.slice(1).split("/").map(
              item => item.replace(/~1/g, "/").replace(/~0/g, "~"));
            deepest = Math.max(deepest, parts.length);
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
        // The strict JSON decoder counts all nested objects and arrays and rejects
        // depth > 32. A fully populated scalar leaf at a 33-segment JSON Pointer
        // requires at least 33 containers regardless of field naming.
        if (deepest > 32)
          warnings.push(stage + " requires at least " + deepest + " nested JSON containers");
        // A field used as a scalar in one comparison cannot simultaneously
        // be the parent object of another comparison's field. Likewise, a
        // single value cannot be both a number and a calendar date. Detect
        // these per stage; two different stage documents may legitimately
        // reuse a JSON Pointer with different types/structures.
        // JSON.stringify(parts) preserves escaped pointer segment boundaries
        // and cannot conflate /a~1b with /a/b.
        const leaves = new Map();
        for (const rule of draft) {
          if (!rule.stages.includes(stage)) continue;
          for (const pointer of [rule.left, rule.right]) {
            const parts = pointer.slice(1).split("/").map(
              item => item.replace(/~1/g, "/").replace(/~0/g, "~"));
            const key = JSON.stringify(parts);
            if (!leaves.has(key)) leaves.set(key, {parts, types: new Set()});
            leaves.get(key).types.add(rule.value_type);
          }
        }
        let mixedTypes = 0;
        const scalarParents = new Set();
        for (const {parts, types} of leaves.values()) {
          if (types.size > 1) mixedTypes++;
          for (let i = 1; i < parts.length; i++) {
            const ancestor = JSON.stringify(parts.slice(0, i));
            if (leaves.has(ancestor)) scalarParents.add(ancestor);
          }
        }
        if (mixedTypes)
          warnings.push(stage + " has " + mixedTypes + " field(s) used as both number and calendar date");
        if (scalarParents.size)
          warnings.push(stage + " has " + scalarParents.size + " scalar/object JSON pointer conflict(s)");
        // A rule that compares a field to ITSELF cannot measure two values.
        // Canonicalize decoded JSON Pointer segments, not display strings:
        // /a~1b is distinct from /a/b. Stage scopes are independent.
        let impossibleSelf = 0, vacuousSelf = 0;
        for (const rule of draft) {
          if (!rule.stages.includes(stage)) continue;
          const canonical = pointer => JSON.stringify(pointer.slice(1).split("/").map(
            item => item.replace(/~1/g, "/").replace(/~0/g, "~")));
          if (canonical(rule.left) !== canonical(rule.right)) continue;
          if (["lt", "gt", "ne"].includes(rule.operator)) impossibleSelf++;
          else if (["le", "ge", "eq"].includes(rule.operator)) vacuousSelf++;
        }
        if (impossibleSelf)
          logicWarnings.push(stage + " has " + impossibleSelf +
            " self-comparison(s) that always violate for valid values (lt/gt/ne)");
        if (vacuousSelf)
          logicWarnings.push(stage + " has " + vacuousSelf +
            " self-comparison(s) that cannot detect a comparison difference (le/ge/eq)");
        // Detect unsatisfiable conditions on the SAME pair of typed fields.
        // Normalize reversed operands and decoded JSON Pointer segments,
        // then intersect their allowed ordering signs (<, =, >).
        // Separate bounded graph analysis below also detects longer
        // directed ordering cycles and indirectly forced equality.
        const canonicalPointer = pointer => JSON.stringify(pointer.slice(1).split("/").map(
          segment => segment.replace(/~1/g, "/").replace(/~0/g, "~")));
        const allowedSigns = {
          lt: ["lt"], le: ["lt", "eq"], eq: ["eq"],
          ne: ["lt", "gt"], ge: ["eq", "gt"], gt: ["gt"]
        };
        const reversed = {lt: "gt", le: "ge", eq: "eq", ne: "ne", ge: "le", gt: "lt"};
        const pairs = new Map();
        for (const rule of draft) {
          if (!rule.stages.includes(stage)) continue;
          const left = canonicalPointer(rule.left);
          const right = canonicalPointer(rule.right);
          if (left === right) continue; // already counted as a self-comparison
          const reverse = left > right;
          const pair = reverse ? [right, left] : [left, right];
          const key = JSON.stringify([rule.value_type, ...pair]);
          const signs = new Set(allowedSigns[reverse ? reversed[rule.operator] : rule.operator]);
          const existing = pairs.get(key);
          pairs.set(key, existing
            ? new Set([...existing].filter(sign => signs.has(sign)))
            : signs);
        }
        const contradictoryPairs = [...pairs.values()].filter(signs => signs.size === 0).length;
        if (contradictoryPairs)
          logicWarnings.push(stage + " has " + contradictoryPairs +
            " pair(s) of fields with mutually incompatible comparison conditions");
        const {orderCycles, forcedEqualDifferences} = multiFieldConflicts(draft, stage);
        if (orderCycles)
          logicWarnings.push(stage + " has " + orderCycles +
            " impossible multi-field ordering cycle(s) involving 3+ fields");
        if (forcedEqualDifferences)
          logicWarnings.push(stage + " has " + forcedEqualDifferences +
            " not-equal comparison(s) contradicted by equality forced through 3+ fields");
      }
      logicWarning.hidden = !logicWarnings.length;
      logicWarning.textContent = logicWarnings.length
        ? "Logical comparison warning: " + logicWarnings.join("; ") +
          ". Self-comparisons test a field against itself; conflicting rules for the same two" +
          " fields can also require mutually impossible results, including with reversed operands." +
           " Longer ordering cycles and indirect equality conflicts may also be impossible." +
          " Missing or invalid field values may still be reported. Review pointers, operators" +
          " and stages. Draft and saved tenant remain unchanged."
        : "";
      limitWarning.hidden = !warnings.length;
      limitWarning.textContent = warnings.length
        ? "JSON structure warning: " + warnings.join("; ") +
          ". The shared Engine accepts at most 1,000 keys in any one JSON object, including nested objects, and at most 32 nested JSON containers. A fully populated document with these fields cannot pass. Use nested objects with no more than 1,000 children each and keep nesting within 32 levels, or split the stages; the draft has not been modified." +
          " Resolve incompatible number/date fields or scalar/object paths by assigning distinct pointers or stages."
        : "";
      drawRows();
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
         (tenant.record_rules || []).some(rule => rule.rule_id === "business." + id)) ||
        otherPendingIds().has(id);
      if (duplicate) { message.textContent = "That comparison ID is already used. Choose a different ID."; return; }
      const item = {rule_id: id, left: controls.left.value, right: controls.right.value,
                    operator: controls.operator.value, value_type: controls.type.value, stages: selected};
      if (editing !== null) draft = draft.map(rule => rule.rule_id === editing ? item : rule);
      else draft.push(item);
      clear(); draw(); changed(); message.textContent = "Comparison pending. Save checks to apply it.";
    });
    draw();
  }
  window.comparisonEditor = {render, delta, actions, otherFamilyDraftHasId};
})();
