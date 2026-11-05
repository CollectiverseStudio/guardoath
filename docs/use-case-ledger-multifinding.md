# Ledger validation — full findings, exact arithmetic, and safe failure

**Category:** Business rules / Ledger. **Existing checker:** `guardoath/data_quality.py::ledger_check`. **Configuration:** `policies/ledger-batch.json`, `ledger-enforce-v1`. This does not create a new checker, authorize payments, verify source authenticity, or detect fraud.

## Use cases and acceptance criteria

| Case | Synthetic example | Expected behavior |
|---|---|---|
| Exact minor-unit arithmetic | Debit 0.10 plus 0.20, credit 0.30 | Complete, Allow; no float rounding |
| Zero-balanced ledger | Debit 0.00 and credit 0.00 | Complete, Allow |
| Maximum supported amount | Debit and credit 999999999999.99 | Complete, Allow; no 64-bit float |
| Amount mismatch | Debit 0.10, credit 0.11 | `business.ledger_unbalanced` |
| Currency mismatch | Currency EUR when policy is USD; sums match | `business.ledger_currency` |
| Two simultaneous findings | EUR plus mismatched totals | Both finding IDs, not first only |
| Duplicate entry IDs | Two entries named `same` | Incomplete; `ledger_duplicate_entry`; fail-closed Block |
| Missing debit/credit counterpart | Debit entries only | Incomplete; `ledger_sides_required`; Block |
| Invalid decimal grammar | Leading zero, negative amount, exponent, wrong scale or more than 12 integer digits | Incomplete; `ledger_invalid_amount`; Block |
| Numeric rather than string amount | `amount:1.0` | Incomplete; `ledger_invalid_document`; Block |
| Duplicate JSON object key | Two currency keys in one payload | Incomplete; `ledger_invalid_document`; Block |
| Truncated JSON | Unclosed entries list | Incomplete; `ledger_invalid_document`; Block |
| Non-JSON input | Free-form text submitted to required Ledger checker | Skipped checker, incomplete evaluation, Block |
| Private submitted values | Synthetic invalid or violating values | Do not echo raw amount or source document in findings |

The tests at `tests/test_ledger_multifinding_regression.py` verify these conditions on both **Input** and **Output** through the same shipped checker and Engine. A completed finding under `ledger-enforce-v1` triggers Input **Flag** or Output **Block**, while required-check errors and skips use the default **Block** failure action on both stages.

## Data contract and limits

A ledger packet contains an uppercase three-letter `currency` and an `entries` array with unique ASCII `entry_id`, `side` (`debit` or `credit`), and **string** `amount`. `decimal_places=2` enforces exactly two fractional digits and at most 12 integer digits. The checker sums integer minor units with no rounding; the tenant's `max_entries` is an operational bound (100 for this profile), not a financial-policy threshold. No exchange-rate conversion, negative reversal, balance authorization, cross-document reconciliation, or external account verification is performed.

## Evidence and release

- Implementation test: `tests/test_ledger_multifinding_regression.py`.
- Existing APIs/evals: `tests/test_data_quality.py`, `evals/ledger.json`.
- Existing user instructions: `docs/user-guide.md`, `docs/data-quality-coverage.md`.
- New regression tests must pass on the **exact implementation SHA** and require 13/13 Tests, 3/3 CodeQL, Director-approved AWS release and 15/15 live checks before declaring production acceptance. Test-only changes do not indicate a deployment.

This documentation deliberately distinguishes syntactic/arithmetic consistency from regulatory or accounting correctness.
