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
| `check_lexical_guards` | none (161 wording patterns; off for demo users until turned on) |
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

## Profiles (removed for tenants, 2026-11-02)

A tenant key runs the record's own check fields, rule settings and check settings, and nothing else.
There are no example profiles and no default profile to choose: a check request leaves `policy_version`
out or sends `tenant`, and any other value is refused (404, not counted). `default_profile` is no longer
a field and cannot be changed (422).

Records stored before the change may still contain `default_profile`. When such a record is read:

- `"tenant"` (the record's own checks) is dropped; nothing changes.
- On a demo user any value is dropped; the demo user runs its record's checks.
- On a customer record any other value stops the record from loading ("needs a migration review"), so a
  customer's checks never change without review.

Setting `GUARDOATH_TENANT_PROFILE_FILE` stops startup with an error, so an old configuration is not
silently ignored. The example profiles in `policies/playground.json` remain for the local developer
launcher and the evaluations only.
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
  profile** changes the default. The checks panel follows the selected profile: for `tenant` it lists all 17
  check fields (Off, Block, Flag, Allow, and Redact for pii) and **Save checks** saves the changed fields; for
  an example profile it shows that profile's checks and their input and output actions, which cannot be
  changed. **Reset to defaults** (demo users) restores the demo values. Paid outside checks cannot be turned
  on for demo users.
- **Local launcher:** `python start_dashboard.py` turns on demo users with a new secret on every start.

## Control registry

`guardoath/control_registry.py` lists every control a tenant can turn on, in one hierarchy:

- **Domain:** a broad responsibility, such as *Privacy and personal data*. Two domains are planned and have
  no controls yet: *Human safety* (`docs/human-safety-scenarios.md`) and *Structured security events*
  (`guardoath/sigma_rules/`).
- **Control:** one field on the tenant record, `check_<control_id>`. It has a plain-language outcome, a
  use case (why you would turn it on), its limits, a severity, a recommended action, the actions it
  supports and the stages it runs on.
- **Rule:** one finding a control can report, such as `pii.iban_code` or `agent.git_force`. Each rule has
  a title, a use case, an example it reports and a look-alike it does not (`guardoath/rule_catalog.py`).
- **Detector:** the code that runs for the control and the version the running checker reports.
- **Evidence:** examples it flags, look-alikes it does not flag, harmless content it still flags (known
  false positives), risky content it does not catch (known misses), unit tests and evaluation files.

`GET /v1/controls` returns the registry. It needs no key and holds no tenant data. The registry only
describes: the tenant record alone decides which controls run and with what action, and the recommended
action is never applied for you.

`tests/test_control_registry.py` runs every example against the real detectors, using the demo user
settings with only that control on, and checks that every declared test and evaluation file exists and
that the guide has a section for every control. Maturity is reported per control (`documented`,
`implemented`, `unit_tested`, `integrated`, `evaluated`, `available` in the running service), and
`deployed` is left empty because the registry cannot see the live site.

## Per-rule settings

A check runs many rules. 397 catalog rules can be set one by one today (2026-11-04), plus each record's own
comparisons and required sections (below):

| Check | Rules |
|---|---|
| `lexical_guards` | 161, including 40 agent-configuration settings such as `verify_tls=false` |
| `pii` | 97 privacy types, 4 of them only with `detect_labeled_sensitive_data` |
| `encoded_pii` | 97: each privacy type inside a labeled Base64 or hex payload (`pii.encoded_<type>`) |
| `secrets` | 17, 1 of them only with `detect_secret_assignments` |
| `evidence` | 8 citation findings |
| `text_security` | 4 |
| `graph` | 3 |
| `schema`, `tool_calls`, `ledger` | 2 each |
| `matched_outcomes`, `timestamps`, `integrity`, `cross_field` (malformed JSON) | 1 each |

`GET /v1/controls` lists every catalog rule under its control, with its title, use case, severity, example,
look-alike, `content_format` (the document checks' examples are JSON) and `needs_checks` (the 4 labeled-field
encoded rules need Personal data on too: the record passes `detect_labeled_sensitive_data` only with the `pii` check).

### The record's own rules: field comparisons and required sections

Each field comparison on a record is a rule `business.<rule_id>` (check `cross_field`), and each required section a
rule `completeness.<rule_id>` (check `completeness`). They are rules of that record only (`guardoath/record_rules.py`):

- **Listed for the tenant only.** `GET /v1/applications/current` returns them in `tenant.record_rules`, in the same
  shape as catalog rules plus `configuration` (pointers, operator, type and stages, or label and minimum length),
  `stages` and `record_rule: true`. The public `GET /v1/controls` never lists them.
- **Set like any rule.** `rule_actions` may name them (`off`, `allow`, `flag`, `block`). A record's `rule_actions`
  may name only catalog rules and its own comparisons and sections, never another tenant's, and a setting cannot
  outlive its rule: removing a comparison while keeping its setting is refused (422; send both changes together).
- **Ids on a tenant record** are lowercase letters, digits and `_` (`^[a-z][a-z0-9_]{0,79}$`) and may not name a
  catalog rule (`business.ledger_currency`, `business.ledger_unbalanced`, `business.invalid_json`). Engine policy files
  keep the wider pattern.
- **Examples** are generated from the configuration: a JSON document the comparison reports and a look-alike it does
  not (other comparisons on the record get passing values where their fields do not overlap); for sections, labeled
  text with the section left out, and with every section present. When a comparison's two pointers overlap no example
  is generated.
- **Limits** are the checks' own: at most 50 comparisons and 50 sections; values come from the submitted JSON only
  (see `docs/field-comparisons-implementation-plan.md` for what comparisons do not prove).

`tests/test_record_rules.py` covers all 30 templates in `docs/field-comparisons-rule-inventory.json` and the demo
users' comparisons and sections, per-rule actions, unknown and removed ids, colliding ids, tenants sharing one engine,
and the API.

The record field `rule_actions` maps a rule id to one of:

| Value | Effect |
|---|---|
| `off` | The rule's findings are dropped. A check left with no findings has passed. Privacy types that are off are not looked for at all, so they are never redacted, including inside encoded payloads (`encoded_pii`). |
| `block`, `flag`, `allow` | The rule's findings use this action instead of the check's action. The strongest action of all findings decides, as before. |

A rule that is not listed uses its check's action. Rule settings are kept while their check is off and
apply again when it is turned back on.

On the dashboard (since 2026-11-03), a table under the checks (which are alphabetical) lists every rule with four
columns: Rule (title and id), Category (the check it belongs to), Setting, and In effect (for example "Block (from
check)", "Allow (own setting)" or "Off (check is off)"). Select a column heading to sort. Type part of a rule's name
or id (for example `email` or `ip address`), or filter by category or by the setting in effect (or only rules with
their own setting). A rule without its own
setting shows **Same as check (Block)** (or the check's current setting), so a check set to Block blocks every rule
in it until one rule is given its own setting, such as `pii.email_address`: `allow`. A rule cannot be set while its
check is off. When a check is changed to Redact, the own settings of its rules other than Off are cleared. Rules are saved like any other check setting, with
`POST /v1/tenants/current/checks` and a `revision`; for example
`{"revision": 4, "changes": {"rule_actions": {"pii.ip_address": "off", "agent.git_force": "block"}}}`
replaces the whole map. An unknown rule id is refused (422). When the `pii` check redacts, its rules can
only be turned `off`, because redaction replaces every value the check finds.

`tests/test_rule_catalog.py` runs every rule's example (it must be reported) and look-alike (it must not
be) against the real detectors. `tests/test_rule_actions.py` covers saving, refusing and applying rule
settings.

## Stages and message formats

A check runs on both stages (input and output), except (since 2026-11-04) a check whose settings cover one stage
only: `schema` runs on the stages that have a schema (`input_schema`, `output_schema`), `cross_field` on the stages
of its `comparisons`, `completeness` on the stages of its `required_sections`. `azure_prompt_shields` is input only.
Before, such a check also ran on the other stage, found nothing to check there, and blocked every message on it.

Some checks need one message format (`content_format`):

| Needs JSON | Needs text |
|---|---|
| `schema`, `cross_field`, `matched_outcomes`, `evidence`, `tool_calls`, `ledger`, `timestamps`, `graph` | `completeness` |

On a message in the other format the check is skipped. A skipped check makes the result `incomplete`, and the
record's `failure_action` applies (`block` by default, or `flag`), even with no findings. This is deliberate: a
check that is on and cannot run is not a pass. The dashboard warns under each such check while it is on, and a
result shows "Not run: this check needs JSON content". `tests/test_check_stages.py` checks the table above against
each check's code.

## Tenant sign-in

Since 2026-11-02 a customer record's settings change only through a signed-in session; its API keys only
run checks, so a leaked production key cannot change settings or keys.

- Accounts are users of an Amazon Cognito user pool (Lite plan): email, password and a required
  authenticator app. There is no public sign-up; an administrator creates each user with the read-only
  attribute `custom:tenant_id`. One user belongs to one tenant.
- The dashboard signs in with the pool's hosted sign-in page (authorization code with PKCE) and keeps the
  session in the browser tab (sessionStorage). It sends the session in `X-GuardOath-Session`.
- `guardoath/tenant_sessions.py` checks every session in two steps:
  1. **Locally, with no network call:** an RS256 token signed by one of the pool's published keys (JWKS),
     for this pool and app client, an access token, unexpired, with the `aws.cognito.signin.user.admin`
     scope. A forged or altered token stops here. The keys are fetched at most once every 5 minutes, even
     when tokens name keys that do not exist (so a new Cognito key is used within 5 minutes).
  2. **Cognito GetUser**, for a token that passed step 1: confirms that the user still exists and the token
     was not revoked, and gives the tenant. A result is kept for at most 60 seconds per token (never past
     its expiry); a refusal is kept for 60 seconds. At most 60 calls to Cognito a minute and 4 at a time per
     copy of the app (trading sign-in codes counts too); over that the request gets 503 with
     `Retry-After: 5`, never let through.
  The tenant must exist, be active and not be a demo user. The sign-in domain must be the pool's prefix
  domain in the pool's region, and sign-in requests to Cognito never follow redirects.
- Signed in, a user can change the record's checks (`POST /v1/tenants/current/checks`) and manage its API
  keys: `GET /v1/tenants/current/keys`, `POST /v1/tenants/current/keys` (at most 2; the key is shown once)
  and `POST /v1/tenants/current/keys/revoke`.
- Demo users have no accounts. Their demo key still changes their own demo record.
- Settings: `GUARDOATH_COGNITO_POOL_ID`, `GUARDOATH_COGNITO_CLIENT_ID`, `GUARDOATH_COGNITO_DOMAIN`,
  `GUARDOATH_COGNITO_REDIRECT_URI` (all four or none). The deploy template sets them.
