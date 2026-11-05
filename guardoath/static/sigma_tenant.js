"use strict";
// Sigma events use a separate tenant map; a missing rule is Off. Never run
// these event signatures as conversation input/output checks.
window.sigmaTenant = (() => {
  let edits = {};
  let saved = {};
  let generation = 0;
  const sorted = obj => JSON.stringify(Object.entries(obj || {}).sort(([a], [b]) => a.localeCompare(b)));
  const delta = () => sorted(edits) !== sorted(saved) ? {...edits} : null;
  async function render(container, tenant, token, makeHeaders, fetcher, onDirty) {
    const current = ++generation;
    edits = {...(tenant.sigma_rule_actions || {})};
    saved = {...edits};
    const details = document.createElement("details");
    details.id = "tenant-sigma-group";
    details.className = "sigma-tenant-group";
    const summary = document.createElement("summary");
    summary.textContent = "Security event rules (SigmaHQ) — 228 source rules";
    details.append(summary);
    const help = document.createElement("p"); help.className = "hint";
    help.textContent = "Saved on this tenant record: each Windows event rule is Off, Allow, Flag or Block. These do NOT check AI messages. Only server-owned synthetic event evaluation is wired; real event ingestion is not active.";
    details.append(help);
    const search = document.createElement("input"); search.id = "sigma-rule-search";
    search.type = "search"; search.placeholder = "Search event rules";
    search.setAttribute("aria-label", "Search security event rules");
    details.append(search);
    const status = document.createElement("p"); status.id = "sigma-rule-count"; status.className = "hint";
    status.setAttribute("role", "status"); details.append(status);
    const table = document.createElement("table"); table.className = "rule-table"; table.id = "sigma-rule-table";
    const header = table.createTHead().insertRow();
    for (const text of ["Security event rule", "Event action", "Use case / source"]) {
      const th = document.createElement("th"); th.scope = "col"; th.textContent = text; header.append(th);
    }
    const tbody = table.createTBody();
    const wrapper = document.createElement("div"); wrapper.className = "rule-table-wrap"; wrapper.append(table);
    details.append(wrapper);
    container.append(details);
    let data;
    try {
      const response = await fetcher("/v1/sigma/catalog", {
        cache: "no-store", headers: await makeHeaders(token)
      });
      if (!response.ok) throw new Error("catalog unavailable");
      data = await response.json();
    } catch (_error) {
      status.textContent = "The Sigma catalog could not be loaded. No settings were changed.";
      return;
    }
    if (current !== generation || !details.isConnected) return;
    const rows = [];
    for (const rule of data.rules || []) {
      const row = tbody.insertRow(); row.dataset.sigmaId = rule.rule_id;
      const titleCell = row.insertCell();
      const title = document.createElement("strong"); title.textContent = rule.title;
      const id = document.createElement("code"); id.className = "rule-id"; id.textContent = rule.rule_id;
      titleCell.append(title, document.createElement("br"), id);
      const setting = row.insertCell();
      const select = document.createElement("select"); select.dataset.sigmaRule = rule.rule_id;
      select.id = "sigma-rule-action-" + rule.rule_id;
      select.setAttribute("aria-label", "Event action for " + rule.title);
      for (const [value, label] of [["", "Off"], ["block", "Block"], ["flag", "Flag"], ["allow", "Allow"]])
        select.append(new Option(label, value));
      select.value = edits[rule.rule_id] || "";
      select.addEventListener("change", () => {
        if (select.value) edits[rule.rule_id] = select.value;
        else delete edits[rule.rule_id];
        update(); onDirty();
      });
      setting.append(select);
      const use = row.insertCell(); use.textContent = rule.use_case;
      const source = document.createElement("small");
      source.textContent = " · " + rule.product + " / " + rule.category;
      use.append(source);
      const controls = document.createElement("div");
      controls.className = "sigma-sample-controls";
      const result = document.createElement("span");
      result.setAttribute("role", "status");
      result.className = "hint";
      for (const [sample, label] of [["positive", "Test match"], ["lookalike", "Test look-alike"]]) {
        const button = document.createElement("button");
        button.className = "secondary";
        button.type = "button";
        button.textContent = label;
        button.addEventListener("click", async () => {
          if (delta() !== null) { result.textContent = "Save the tenant rule settings before testing."; return; }
          if (!saved[rule.rule_id]) { result.textContent = "This tenant rule is Off. Enable and save it first."; return; }
          button.disabled = true;
          result.textContent = "Running bundled synthetic " + sample + " event...";
          try {
            const content = JSON.stringify({rule_id: rule.rule_id, sample});
            const response = await fetcher("/v1/sigma/demo", {
              method: "POST", cache: "no-store", headers: await makeHeaders(token, content), body: content
            });
            if (!response.ok) throw new Error("Test unavailable (HTTP " + response.status + ")");
            const data = await response.json();
            result.textContent = data.findings.length + " matching enabled rule(s)" +
              (data.sample_rule_matched ? " · Selected rule matched" : " · Selected rule did not match") +
              " · Recommended " + data.recommended_action + " · Effective " + data.effective_action +
              " · Bundled synthetic event only";
          } catch (error) { result.textContent = error.message || "Synthetic test could not complete."; }
          finally { button.disabled = false; }
        });
        controls.append(button);
      }
      controls.append(result);
      use.append(controls);
      rows.push({row, query: [rule.title, rule.rule_id, rule.use_case, rule.category].join(" ").toLowerCase()});
    }
    const update = () => {
      const needle = search.value.trim().toLowerCase(); let visible = 0;
      for (const entry of rows) {
        entry.row.hidden = !entry.query.includes(needle);
        if (!entry.row.hidden) visible++;
      }
      status.textContent = visible + " of " + rows.length + " event rules shown; " +
        Object.keys(edits).length + " enabled on this tenant.";
    };
    search.addEventListener("input", update);
    update();
  }
  return {render, delta};
})();
