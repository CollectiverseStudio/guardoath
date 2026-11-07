# Create and edit comparisons in the dashboard

Connect to your own record and open **Field comparison rules**. This editor
creates number and calendar-date comparisons; advanced arithmetic, identity,
conversion and signed-reference definitions retain their existing configuration.

For an order that must precede shipment, enter ID `order_before_ship`, left JSON
pointer `/order_date`, **≤ at most**, right pointer `/shipment_date`, and
**Calendar date (YYYY-MM-DD)**. Choose Input, Output or both, then **Add to
pending changes**. Turn on Field comparisons with the desired action on each
selected stage and select **Save checks**. Only a successful save changes what
runs. `{"order_date":"2026-11-03","shipment_date":"2026-11-01"}` then reports
`business.order_before_ship`; swapping the dates is clean for that rule.

Number comparisons use the same workflow, for example `/refund_amount` ≤
`/purchase_amount`. JSON pointers support escaped field names: `~1` is a slash
inside a field name and `~0` is a tilde. Values must meet the existing checker
contract: numbers are numeric JSON values; dates are YYYY-MM-DD strings.

**Edit** changes pointers, operator, type or selected stages while keeping a
stable rule ID. **Cancel edit** discards only the unfinished form edit. **Remove**
marks a rule for deletion; Save checks applies it. Removing a rule, or removing
one of its stages, clears its now-inapplicable per-rule override while retaining
other comparisons and settings. If no comparison rules remain on a stage, turn
Field comparisons off on that stage before saving; the editor never silently
disables a check. Draft additions/removals are not active until
saved. The existing rule table, examples and finding IDs refresh from the saved
record after a successful save.

The form catches duplicate IDs, malformed pointers and an empty stage selection.
The authenticated server remains authoritative for schema, tenant ownership,
policy, size limits and revision admission. If someone else changes the record,
a stale save is refused and the current record is reloaded; review and reapply
your edit. Customer sign-in requirements and demo-key permissions are unchanged.
The authenticated tenant summary now includes only the ordinary `comparisons`
definitions needed by this editor; it does not expose signed reference snapshots,
source metadata or host signatures.

## Validation boundary

`tests/test_comparison_editor_contract.py` exercises actual authenticated create,
reload, typed/stage edit, removal, stale revision refusal, two-stage Block/Flag
and clean checks, usage attribution and another tenant remaining unchanged. The
asset test verifies the new script is versioned and served under the existing
CSP. `tests/browser/field_comparison_editor.spec.js` drives the real dashboard
form and real save/reload/check endpoints (no mocked tenant persistence), checks
invalid pointer feedback, stage override cleanup and preservation of existing
rules. Browser execution requires CI; no local browser pass is claimed.
