# Action group: proposed tool calls

Version 0.22.0. This checker validates declared names and argument schemas. It
never executes, authorizes or dispatches a tool, follows a URL, opens a file,
contacts a model/provider, sends a message or creates an actual refund.

## Contract and trust boundary

A trusted versioned policy configures 1–20 `tool_calls.tools`, each with a distinct,
case-sensitive ASCII identifier `name` and an `arguments_schema` JSON Schema.
A `tool_calls` rule and its options must be supplied together. Schema validation
uses the existing Draft 2020-12 validator and supported format predicates;
startup rejects invalid schemas, remote references and unsupported format names.
Local references work. The engine snapshots schemas, so later mutation of the
caller's original objects cannot change the active version.

Input/output content is JSON with exactly `tool_calls`, an array of up to ten
records. Each record has exactly name and arguments. Name uses letters/digits/
underscore, beginning with a letter, at most 50 characters. Arguments must be an
object. Empty call lists explicitly mean no proposed calls; they are not proof
that unseen code or actual execution is safe. Caller-supplied tool catalogs and
unexpected envelope/record fields are rejected.

Each syntactically valid name must match an allowed name exactly. Arguments for
allowed tools must satisfy that tool's schema. Prefixes, case variants and unknown
names receive no implicit permission. Multiple invalid calls yield deduplicated
finding classes, not messages containing private tool/argument names or values.
A malformed record makes the entire required assessment incomplete rather than
silently validating its subset.

The examples allow synthetic lookup_order and request_refund proposals. The
refund schema requires a configured order-ID shape, whole-number amount in 0–100,
reason from a fixed enum and USD currency. These are illustrative submitted-field
constraints, not authoritative account limits, refund eligibility or approval.
No tool implementation exists or runs behind these examples.

## Results

| Rule/code | Meaning |
| --- | --- |
| actions.unknown_tool | A proposed name is outside the allowlist |
| actions.invalid_arguments | An allowed tool's arguments fail its configured schema |
| tool_calls_invalid_document | Invalid bounded JSON/shape/type/call-count or a lossy numeric token |
| tool_calls_json_required | No JSON tool-call packet; required check incomplete |

Findings are high-severity review signals with fixed safe explanations. Input
findings recommend flag and output findings recommend block in the observe sample;
effective action remains allow. Dashboard findings/incomplete results hold the
answer. Enforce sample variants apply recommendations. Required errors obey the
configured failure action. No tool dispatch is implicit in an allow response.

The strict parser rejects duplicate keys, nonfinite/overflowing numbers and the
existing JSON resource bounds. A second decode rejects float tokens whose decimal
value cannot survive the ordinary float/string round trip, including tiny values
that underflow and excessive encoded exponents. Integers retain their Python
integer precision. This prevents silently rounding a submitted amount over a
boundary, but does not turn JSON Schema arithmetic (for example decimal
multipleOf) or trusted schema-number decoding into an exact monetary engine.
Use reviewed representations/rules for real financial operations. Trusted schema
regex/validation CPU is not independently process-isolated.

An allowed name and schema-valid arguments do **not** establish authorization,
object ownership, safe destinations/commands, sandbox safety, source-code syntax,
absence of malware or injection, appropriateness of side effects, idempotency,
transaction state, or current business limits. The actual tool runner must enforce
those boundaries immediately before execution. Local PII/secrets checks are
separate if the application requires them for these packets.

## Complete batch

```powershell
py -3.13 start_dashboard.py --evaluate --suite actions
```

Or:

```bash
python -m valinator.evaluate --cases evals/actions.json --policies policies/actions-batch.json
python -m pytest tests/test_tool_calls.py -q
```

The 50 scenario definitions run on both stages: 100 expectations, including 28
intentional incomplete cases. Tests cover exact allowlisting, schema constraints,
local references/formats, duplicate/malformed envelopes, call-count boundaries,
ambiguous numeric tokens, stage/mode behavior, private-data omission, snapshot
isolation and attempts to replace the allowed catalog. Additional tests deny both
network connections and subprocess invocation. Browser examples demonstrate all
major paths without executing anything. No new dependency or paid service is used.
