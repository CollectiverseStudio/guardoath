# Trusted Sigma routing configuration preview

`RoutingTable.plan()` describes which configured rule IDs belong to a trusted
Windows logsource. It does not inspect event content, run detectors, launch regex
workers, authenticate a tenant or authorize an operation. Its immutable result is
not an admission token, cached permission decision or evidence that an event was
validated. No hosted endpoint or tenant schema is added.

```python
from guardoath.sigma_rules.routing import load_routing_table

table = load_routing_table()
preview = table.plan(trusted_logsource=source_from_host,
                     allowed_rule_ids=current_ids_from_host)
print(preview.as_dict())
# Later, authenticate/resolve the current host configuration again:
findings = table.evaluate(event, trusted_logsource=current_source_from_host,
                         allowed_rule_ids=current_ids_from_host)
```

The returned `RoutingPlan` holds product, category, `selected_rule_ids` and
`excluded_rule_ids` as immutable fields/tuples. Both ID groups preserve configured
order; excluded IDs are known configured rules for a different source, not unknown
IDs silently ignored. `as_dict()` returns fresh JSON-ready copies and explicitly
marks `evaluation_performed=false`. Copy edits cannot change the plan or table.
Rule definitions remain available through the existing copied `source_for()`
descriptors, including original logging prerequisites.

For the current 203-rule inventory, an all-ID PowerShell-script preview selects
125 script IDs and excludes 78 process-creation IDs; a process preview reverses
those counts. A host may configure a bounded mixed-source list, but no preview or
evaluation may select more than 128 rules for one source. Invalid source shape,
unknown/duplicate IDs, missing configured routes and oversized selected sets are
explicit errors. Oversized source maps reject before key enumeration. Values must
come from authenticated host configuration, never event/request JSON.

Planning and evaluation share one selector. A standalone preview has a bounded
preparation deadline (`timeout_seconds=1.0`, positive finite maximum 5 seconds).
Inside an existing budget it inherits the outer deadline and cannot refresh that
deadline or its consumed worker quota. Evaluation uses the selector inside its
own existing aggregate context, then calls the event evaluator exactly once;
it never accepts a prior plan as a shortcut. All prior event bounds, worker caps,
timeouts, attribution and no-partial-result behavior remain unchanged.

Tests cover all 203 original positive/look-alike pairs, exact source partitions,
configured order, malformed source/ID/deadline settings, immutable exports, no
matcher/worker calls, the 128/129 boundary, and inherited time/work budgets. These
are executable contract fixtures, not detector effectiveness evidence. The original 128 definitions remain unchanged; batch 007 adds 25 exact original
process rules under the same routing contracts. The Director's pending
packaging adoption and hosted authentication/routing/tenant integration remain
separate release requirements.
