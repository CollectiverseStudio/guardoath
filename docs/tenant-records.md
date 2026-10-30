# Tenant records

Each tenant is one record. Everything about the tenant is on it:

- **Account:** `tenant_id`, `display_name`, `status`, `billing_mode`, `payer_id`.
- **Keys:** `key_hashes`, up to two SHA-256 hashes, so a key can be rotated without downtime. The key
  itself is shown once when it is created and is never stored.
- **Monthly allowance:** `monthly_allowance` and `overage`. Customers use `allow_and_record`: checks past
  the allowance still run and are recorded for billing. Demo users use `block`: their checks stop at the
  allowance.
- **Usage:** `usage`, one counter per UTC month (`"2026-10": 1532`).
- **One field per check**, listed below.
- **Check settings:** what some checks need to run (schemas, comparisons, required sections, and so on).

## Check fields

Each check is a field named `check_<id>`.

| Field value | Effect |
|---|---|
| no value | The check does not run. |
| `block` | The check runs. A finding blocks the message or answer. |
| `flag` | The check runs. A finding is reported and flagged. |
| `allow` | The check runs. A finding is reported and the message or answer is allowed. |
| `redact` | `check_pii` only: detected identifiers are replaced, and the selected checks run again on the result. |

One value covers both the user's message (input) and the AI's answer (output). The exception is
`check_azure_prompt_shields`, which checks input only.

| Field | Needs these settings before it can run |
|---|---|
| `check_pii` | none (options: `detect_labeled_sensitive_data`, `privacy_patterns`, `disabled_privacy_entities`, `privacy_min_score`) |
| `check_encoded_pii` | none |
| `check_secrets` | none (options: `detect_secret_assignments`, `inspect_encoded_secrets`, `confidential_terms`) |
| `check_text_security` | none |
| `check_schema` | `input_schema` or `output_schema` |
| `check_cross_field` | `comparisons` |
| `check_completeness` | `required_sections` |
| `check_matched_outcomes` | `matched_outcomes` |
| `check_evidence` | `evidence` |
| `check_tool_calls` | `tool_calls` |
| `check_ledger` | `ledger` |
| `check_timestamps` | `timestamps` |
| `check_graph` | `graph` |
| `check_integrity` | `integrity` |
| `check_content_safety` | none; calls a paid outside provider and runs only after the local checks pass |
| `check_azure_prompt_shields` | none; calls a paid outside provider and runs only after the local checks pass |

Rules:
- At least one check must have a value.
- Settings stay on the record while their check is off, and they are used again when it is turned back on.
- A record with an invalid setting is rejected, even if that setting's check is off.
- `mode` (`enforce` or `observe`), `timeout_ms` and `failure_action` apply to all of the tenant's checks.

## Demo users

`demo1` to `demo10` are ordinary tenant records. Their starting values are in
`guardoath/demo_tenant_defaults.json`:

- **On, with `block`:** pii, encoded_pii, secrets and text_security.
- **Off, with settings filled in** so any of them can be turned on: schema, cross_field, completeness,
  matched_outcomes, evidence, tool_calls, ledger, timestamps, graph and integrity.
- **Never on:** content_safety and azure_prompt_shields (paid providers).
- **Limits:** 2,000 checks per month each, hard limit (`overage: block`), and no keys.
- **Reset:** a reset sets every check field and check setting back to these values.
