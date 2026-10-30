# Tenant records

Each tenant is one record. Everything about the tenant is on it:

- **Account:** `tenant_id`, `display_name`, `status`, `billing_mode`, `payer_id`.
- **Keys:** `key_hashes`, up to two SHA-256 hashes, so a key can be rotated without downtime. The key
  itself is shown once when it is created and is never stored.
- **Monthly allowance:** `monthly_allowance` and `overage`. Customers use `allow_and_record`: checks past
  the allowance still run and are recorded for billing. Demo users use `block`: their checks stop at the
  allowance.
- **Usage:** `usage`, one counter per UTC month (`"2026-10": 1532`).
- **Default profile:** `default_profile`, the profile used when a check request names none (see
  [Profiles](#profiles)). It starts as `tenant`, the record's own checks.
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

## Profiles

A profile is a named set of checks. Every tenant can use:

- **`tenant`**: the record's own check fields and settings. These are the only checks a tenant changes
  one by one.
- **The example profiles** in `policies/playground.json` (23 profiles; `GUARDOATH_TENANT_PROFILE_FILE`
  chooses another file). Each runs exactly as it is defined there, including its own mode, deadline and
  actions per stage. Demo users do not get `content-safety-v1`, which calls a paid outside provider.

A check request can name a profile in `policy_version`. If it names none, the record's
`default_profile` is used. A tenant changes its default profile like a check field
(`{"default_profile": "pii-v1"}`); only a profile the tenant can use is accepted. The server does not
start if the example profile file is missing.

## Demo users

`demo1` to `demo10` are ordinary tenant records. Their starting values are in
`guardoath/demo_tenant_defaults.json`:

- **On, with `block`:** pii, encoded_pii, secrets and text_security.
- **Off, with settings filled in** so any of them can be turned on: schema, cross_field, completeness,
  matched_outcomes, evidence, tool_calls, ledger, timestamps, graph and integrity.
- **Never on:** content_safety and azure_prompt_shields (paid providers).
- **Limits:** 2,000 checks per month each, hard limit (`overage: block`), and no keys.
- **Default profile:** `tenant`.
- **Reset:** a reset sets every check field, check setting and the default profile back to these values.

## Hosted API

Set `GUARDOATH_TENANT_STORE` to turn on tenant records:

- `memory`: records live for the life of the server process. For local use and tests.
- `dynamodb`: records are kept in the DynamoDB table named by `GUARDOATH_TENANT_TABLE` (partition key
  `tenant_id`, string). Needs the `aws` extra and AWS credentials from the normal SDK chain (on Lambda, the
  function's role). One item per tenant: `record` (the record's fields as JSON), `revision`, and `usage`
  (a map of month to count). Every record change is conditional on `revision`, a check is counted with one
  conditional update of `usage` only (so counts and record changes never overwrite each other), and reads
  are strongly consistent. With a demo secret, missing demo users are created at startup and existing ones
  keep their checks and usage (only their key hash is refreshed). The demo user list is read from the table
  at most once a minute. The table is created by the deployment, not by GuardOath.

The rest of this section applies to both stores. Set `GUARDOATH_DEMO_KEY_SECRET` (at least 32
characters) to create demo1 to demo10 with demo keys. The local single-key mode (`GUARDOATH_API_KEY`)
keeps working alongside it.

| Endpoint | Key needed | What it does |
|---|---|---|
| `GET /v1/demo-users` | no | Lists the demo users. 404 when demo users are off. |
| `POST /v1/demo-users/{tenant_id}/key` (JSON body `{}`) | no | Returns that demo user's API key. Every visitor who picks the same demo user gets the same key. |
| `GET /v1/applications/current` | yes | Every profile the tenant can use (`tenant` first), each check with its action on each stage, the default profile and this month's usage. |
| `POST /v1/checks/input`, `POST /v1/checks/output` | yes | Runs a profile. Use `application_id` = the tenant id. `policy_version` names the profile; leave it out to use the record's default profile. A profile the tenant cannot use gets 404. Local keys must always name a profile. |
| `POST /v1/tenants/current/checks` (JSON `{"revision": n, "changes": {...}}`) | yes, a tenant key | Changes the tenant's own check fields, check settings and default profile. `null` turns a check off. Refused with 409 if the record changed since revision `n`, and with 422 (and a reason) for an invalid value, an account field, or settings the engine rejects. Nothing is stored when a change is refused. Not counted as a check. |
| `POST /v1/tenants/current/reset` (JSON `{}`) | yes, a demo key | Sets a demo user's check fields and settings back to the demo defaults. 403 for customers. |

- **Sending the key:** in an `X-GuardOath-Key: <key>` header, or as `Authorization: Bearer <key>`. If both
  are sent, `X-GuardOath-Key` is used. The hosted site (guardoath.com) runs behind CloudFront, which replaces
  `Authorization` with its own signature, so use `X-GuardOath-Key` there. Requests with a body (POST) to the
  hosted site must also send `x-amz-content-sha256` with the lower-case hex SHA-256 of the exact body bytes;
  CloudFront needs it to sign the request. The dashboard does both.
- **Keys:** tenant keys look like `gok_<tenant_id>_<secret>` and lead to exactly one record. A demo key is
  derived from `GUARDOATH_DEMO_KEY_SECRET` and the tenant id. Only its hash is on the record. Changing the
  secret changes every demo key.
- **Counting:** each check that returns a result (HTTP 200) adds one to the record's count for the UTC month.
  Refused requests (401, 403, 404, 422, 429) and failed checks are not counted.
- **Monthly limit:** a demo user at its limit gets HTTP 429 "Monthly check limit reached" until the next
  month. Customers (`overage: allow_and_record`) are counted past their allowance.
- **Check changes:** a change to a record's check fields applies from the next request.
- **Dashboard:** when demo users are on, the dashboard shows **Or try a demo user**. Choosing one and
  selecting **Get demo API key** shows the key and connects with it. The check profile drop-down lists every
  profile the tenant can use and starts on its default profile. **Default profile** with **Save default
  profile** changes the default. The checks panel follows the selected profile: for `tenant` it lists all 16
  check fields (Off, Block, Flag, Allow, and Redact for pii) and **Save checks** saves the changed fields; for
  an example profile it shows that profile's checks and their input and output actions, which cannot be
  changed. **Reset to defaults** (demo users) restores the demo values. Paid outside checks cannot be turned
  on for demo users.
- **Local launcher:** `python start_dashboard.py` turns on demo users with a new secret on every start.
