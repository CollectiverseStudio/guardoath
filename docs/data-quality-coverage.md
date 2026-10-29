# Local data-quality validation batch

Version 0.24.0 adds four optional, local, model-independent checkers. Each runs
on input and output when selected in a server-owned policy. They have no cloud,
provider SDK, database, file-system upload reader or network dependency. The
conversation default still selects its general-text checks; these data contracts
are focused profiles in Advanced settings.

| Checker / demo profile | Checks | Finding codes | Does not establish |
|---|---|---|---|
| `ledger` / `ledger-v1` | Exact debit/credit line-item sums and configured currency | `business.ledger_unbalanced`, `business.ledger_currency` | Accounting correctness, authentic records, fraud detection, conversion |
| `timestamps` / `timestamps-v1` | Submitted numeric samples ordered by timestamp instants | `structure.timestamp_order` | Freshness, sensor plausibility, source authenticity, cross-request order |
| `graph` / `graph-v1` | Declared directed nodes/edges, dangling references, duplicate edges and cycles | `structure.graph_unknown_node`, `structure.graph_duplicate_edge`, `structure.graph_cycle` | Legal relationships, package/version compatibility, authorization, execution safety |
| `integrity` / `integrity-v1` | Exact UTF-8 or Base64-decoded bytes against a policy-owned SHA-256 digest | `structure.integrity_digest_mismatch` | Trusted provenance, safe/unpoisoned content, signature verification |

## Ledger: validate exact line-item arithmetic

An accounting assistant receives this packet before or after generating a draft:

```json
{"currency":"USD","entries":[
  {"entry_id":"d1","side":"debit","amount":"0.10"},
  {"entry_id":"d2","side":"debit","amount":"0.20"},
  {"entry_id":"c1","side":"credit","amount":"0.30"}
]}
```

It passes because amounts are converted to exact integer minor units before
summing: 10 + 20 = 30. Changing the credit to `"0.31"` produces a balance finding.
Changing currency to `"EUR"` produces a currency finding, even when sums match.
Both findings can appear together. No caller-supplied total is accepted.

Server policy configuration:

```json
{"ledger":{"currency":"USD","decimal_places":2,"max_entries":100}}
```

This must accompany a `ledger` rule. Currency is exactly three uppercase ASCII
letters, compared literally; the checker does not maintain a currency registry.
Scale is 0–4; entry cap is 2–200 (default 100). Each entry has exactly `entry_id`,
`side` and `amount`; both debit and credit sides are required. IDs are unique,
1–80 ASCII letters/digits/underscore/hyphen/dot. Amounts must be strings, 1–12
integer digits and exactly the configured fractional digits. No sign, exponent,
leading zero padding, grouping, numeric coercion or rounding is allowed. Zero
is allowed. Reversals/negative amounts need a separately designed contract.

Malformed documents, duplicate IDs, missing sides, invalid amounts and cap
violations return incomplete assessments with `ledger_invalid_document`,
`ledger_duplicate_entry`, `ledger_sides_required`, `ledger_invalid_amount` or
`ledger_entry_limit`. Non-JSON content is skipped (`ledger_json_required`) and
incomplete when the rule is required. Records are never repaired or posted.

## Timestamps: compare instants without sorting samples

```json
{"samples":[
  {"timestamp":"2026-10-29T10:00:00+01:00","value":12},
  {"timestamp":"2026-10-29T09:01:00Z","value":13}
]}
```

This passes because 10:00 at +01:00 is 09:00 UTC, before 09:01 UTC. Reverse the
records to produce an order finding. Equal instants, including differently
written offsets, fail unless the policy explicitly allows ties.

```json
{"timestamps":{"allow_equal":false,"max_samples":200}}
```

The exact envelope is `samples`; every record has exactly `timestamp` and
`value`. There must be 2–500 records, subject to the configured cap (default
200). Values must be finite JSON numbers; booleans, strings and null are not
numbers here. No conversion to float is performed for integer samples.
Timestamps require an explicit known offset and seconds. Fractions support
1–6 digits; `T`/`Z` may be lowercase. Invalid calendar dates, unknown `-00:00`
offset, missing offset, leap seconds, excess fractional precision and instants
outside the supported datetime range fail incomplete. `timestamps_invalid_document`
and `timestamps_sample_limit` distinguish invalid packets from cap violations;
non-JSON is `timestamps_json_required`.

No sorting, truncation, smoothing, imputation or freshness check is performed.
An old but correctly ordered sequence can pass. Separate requests do not share
state or establish continuity.

## Graphs: reject cycles in an explicitly submitted directed graph

```json
{"nodes":["extract","validate","summarize"],
 "edges":[["extract","validate"],["validate","summarize"]]}
```

This passes. Add `["summarize","extract"]` to produce a cycle finding. Self-loops
also count as cycles. An edge mentioning an undeclared node produces an unknown
node finding; duplicate edges produce a duplicate edge finding. Multiple findings
may coexist, although cycle traversal is withheld when endpoints are unknown.

```json
{"graph":{"max_nodes":100,"max_edges":500}}
```

The envelope has exactly `nodes` and `edges`. Nodes are distinct, case-sensitive
ASCII IDs using the same 1–80 character alphabet as ledger IDs. Edges have
exactly two ID strings. Empty graphs, isolated nodes and disconnected acyclic
components are valid. The node cap is 1–200; edge cap 1–1,000. Absolute document
bounds and strict JSON parsing also apply. Duplicate nodes are an incomplete
`graph_duplicate_node`, oversized graphs `graph_size_limit`, other malformed
packets `graph_invalid_document`; non-JSON is `graph_json_required`.
Traversal is iterative and bounded by nodes plus edges, avoiding recursive
walks. This does not infer dependencies from prose, query a graph database,
validate the legality of relationships or execute a workflow.

## Integrity: use a trusted reference, preserve exact bytes

The demo policy expects the exact UTF-8 text `demo dataset` followed by one LF
newline. The **Clean question / Safe reply** buttons supply those bytes. Removing
the final newline, changing to CRLF, adding a byte-order mark or changing case
produces a mismatch finding. Whitespace and Unicode are never normalized.

A real policy owner computes a reference from independently approved bytes:

```python
import hashlib
trusted_digest = hashlib.sha256(approved_bytes).hexdigest()
```

Configure that 64-character hexadecimal value in `integrity.sha256`, choose
`encoding` (`utf8` or `base64`) and `max_bytes` (1–262,144; default 16,384), and
select an `integrity` rule. Uppercase hex is normalized in policy configuration.
Malformed/missing digests prevent policy validation. The reference digest is
not exposed in application metadata or findings. The API rejects an extra
caller-provided `sha256` field; a digest written inside content is ordinary
untrusted content and cannot replace the server reference.

`utf8` hashes exactly `request.content.encode('utf-8')`. `base64` accepts only a
strict entire-content Base64 string and hashes the decoded bytes, which may be
binary. No `base64:` prefix, whitespace scrubbing or recursive decoding is
performed. `integrity_invalid_encoding` and `integrity_byte_limit` are incomplete
results. The content-format label does not parse or canonicalize bytes; an
integrity-only profile can pass bytes that would fail a JSON schema. Pair it
with the appropriate other checkers when those checks are needed.

The API still accepts bounded text, not streamed uploads or URL/file paths.
No path is opened and no URL fetched. This check is hash equality, not a digital
signature, zero-knowledge proof, decryption, malware scan or poisoning detector.

## API response and stage decisions

Use the same authenticated `/v1/checks/input` and `/v1/checks/output` endpoints.
Select a published policy using `application_id` and `policy_version`; submit
`content` and `content_format`. Configuration stays on the server.
A successfully processed finding or incomplete assessment returns HTTP 200 with
`evaluation_status`, `recommended_action`, `effective_action`, `findings` and
per-checker status/error codes. Invalid API envelopes return safe HTTP 422.
Inspect those fields rather than interpreting HTTP 200 as safe content.

Bundled observe profiles flag findings on input and recommend blocking output;
observe mode's effective action remains allow. Their `*-enforce-v1` equivalents
apply the recommendation. Required errors/skips are incomplete and recommend
blocking. The dashboard holds content on findings or incomplete assessments in
either mode. Local findings/errors also prevent configured external validation
transfer. No ledger rows, samples, node IDs, source bytes or digests are echoed
in findings. Checks never mutate data.

## Regression evidence and use-case mapping

Each checker has **100 saved expectations** (50 distinct cases across input and
output), covering clean cases, findings, malformed/budget failures and enforce
behavior. Deliberately incomplete counts: ledger 68, timestamps 60, graph 52,
integrity 28. These are expected safe failure paths, not successful assessments.
`tests/test_data_quality.py` also exercises all 400 packets through the API plus
configuration, privacy, local execution and external-transfer gate checks.
The dashboard adds eight independent examples per checker per stage, plus
adapted clean examples and browser sequence/hold tests.

Run each batch, or all 14 local suites, without enabling a paid provider:

```powershell
py -3.13 start_dashboard.py --evaluate --suite ledger
py -3.13 start_dashboard.py --evaluate --suite timestamps
py -3.13 start_dashboard.py --evaluate --suite graph
py -3.13 start_dashboard.py --evaluate --suite integrity
py -3.13 start_dashboard.py --evaluate --suite all
```

The combined index now has **1,330 expectations**, including **396 deliberately
incomplete** assessments. Matched expectations are software regression evidence,
not measured detection accuracy or a guarantee of safe data.

UC075 and UC093 now have directed-graph structural controls; semantic/legal or
package-resolution requirements remain planned. UC076 now has within-packet
ordering; cross-request alignment and data repair remain unsupported. UC088 now
sums actual rows exactly; accounting/fraud assessment remains unsupported. UC095
now checks bounded submitted bytes against a trusted policy digest; streamed
uploads and provenance verification remain separate requirements.
