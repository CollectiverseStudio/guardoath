# Valinator user guide

This guide grows with the product. It describes user-facing validation checks,
examples, expected results, configuration, and boundaries. Version: 0.19.0.

A **check** examines an input or proposed AI answer. A **regression case** tests
whether that check behaves as expected on a saved example. Passing a regression
case does not mean its content is safe: the expected outcome can be a finding or
an incomplete evaluation. Internal API/engine tests verify the machinery; they
are not additional content detectors.

## Start here on your Windows computer

In your `valinator-latest` folder:

```powershell
git pull --ff-only
py -3.13 start_dashboard.py
```

The launcher prepares its own environment, prints a fresh application API key,
and opens http://127.0.0.1:8000/dashboard. Copy the key into **Application API key**
and select **Connect**. Keep that PowerShell window open; Ctrl+C stops the server.
A restart gives you a new key. The dashboard checks the message and proposed
answer you provide; it does not generate the AI answer. The default launcher
keeps all checks local and disables Azure, including inherited enable settings.

To run the entire Secrets and confidential information batch, use a second
PowerShell window in the same folder:

```powershell
py -3.13 start_dashboard.py --evaluate --suite secrets
```

This runs **100 saved examples**, opens an HTML report, and preserves JSON/HTML
under `.valinator-reports/`. The first run becomes a reference; later runs compare
with a previous report of the same suite. No server, API key, real credential,
provider account, or paid call is required. This is separate from the smaller
30-case starter suite selected by `--evaluate` without `--suite secrets`.
Use `--no-browser` to print the report location. Mac/Linux users can use
`python3.12 start_dashboard.py` with the same options.

## Test a conversation in four steps

The normal demo selects **Conversation — combined general text checks** for you.
You do not need to choose a policy before entering a question.

1. **Input data:** enter the user's message.
2. **Input test:** select **Run input checks**. The message runs through all three
   implemented general text checkers: supported sensitive identifiers, secrets
   (including quoted assignments, encoded scanning and example confidential
   terms), and bounded text-security signals.
3. **Output (AI) data:** once input passes, your real application would call its
   AI model. In this local demo, paste the proposed answer or use a sample;
   Valinator does not generate an answer or call Tokatlas here.
4. **Output test:** select **Run output checks**. The proposed answer runs through
   those same three checkers. A preview appears only after the sequence completes
   without findings, required-check failures or simulation.

Input findings hold the sequence **even in observe mode**. Correct the message
and rerun input. Editing the input or profile invalidates both stages; editing
only the proposed answer preserves the completed input test and clears output.
Failed, incomplete, simulated or skipped input checks cannot unlock output.
The prepared-example shortcut runs the same sequence, stopping before the output
API request if input is held. This conservative demo gate is distinct from the
API's observe-mode `effective_action: allow`: integrating applications remain
responsible for enforcing their decisions and ordering model calls.

A **policy** is the application's saved bundle of checks, thresholds and actions.
It remains part of each API request so different applications can have different
requirements. The selector now lives under **Advanced: application rules and
focused examples**. Example buttons never switch your profile. Select a focused
profile explicitly for redaction, required-section or business/schema examples.
The active profile and its coverage remain visible outside the advanced panel.

“All checks” means all implemented checks applicable to that application and
content. Schema validation needs your expected JSON structure; business rules and
required sections need your requirements. Running a refund schema on an ordinary
question would be meaningless. These remain focused examples until configured
for your application. Harmful-content and bias checkers are still simulators,
and accuracy, scope and action-safety checks are not implemented. They are not
included in the normal profile or represented as passing. Sensitive identifier
coverage is limited, not comprehensive PHI detection; text-security patterns are
not a complete prompt-injection detector. Expand each result group for limits.

### Input and output examples are independent

Step 1 has **Try an input example**; Step 3 has **Try an output example**.
An input button changes only the user message and its format. It clears both
stages' old results because the conversation input changed. An output button
changes only the proposed answer and its format, preserves the user message and
completed input test, and clears the old output result/preview. Neither set
switches the profile, calls an API or generates a model response. Buttons are
unavailable while checking or before connection. Examples requiring another
profile are hidden; select that profile under Advanced to show them.

| Examples | Input use case | Output use case / expected result |
| --- | --- | --- |
| Clean question / Safe reply | Ordinary question, or a valid request for the active structured/section profile | Safe draft reply; configured checks should complete without findings |
| Email address, Card number, SSN candidate | Question containing a supported synthetic identifier | Reply exposing a supported synthetic identifier; privacy finding |
| Phone number, Birth date, Medical record ID, Insurance member ID, Bank account | Question plus an explicitly labeled synthetic form field | Different draft reply with a labeled field; privacy finding |
| Hidden characters | Question with a zero-width character | Reply with hidden text; text-security finding |
| Encoded text | Question containing an explicit Base64 note | Reply containing a different Base64 note; encoding review finding even if decoded text is benign |
| Credential pattern | Question including an inert generated token pattern | Reply including an inert token pattern; secret finding, no credential verification |
| Quoted password | Configuration-review request with a synthetic literal | Draft answer accidentally includes a different synthetic password literal; secret finding |
| Confidential term | Request mentioning the fictional PROJECT LANTERN | Draft reply mentioning the same configured confidential project; term finding |
| Output-only confidential term | No input button; a stage-specific example | Reply mentions the fictional unreleased pricing plan; output-stage term finding |
| Multiple issues | No input button | Reply includes an email, quoted password and hidden text; findings across all three real local groups |
| Redaction preview | Synthetic intake values, under `redaction-v1` | Draft reply with synthetic identifiers; separate rechecked redaction preview |
| Schema error | Invalid structured request, under `business-v1` or `structure-v1` | Invalid structured answer; schema finding |
| Missing field, Wrong data type, Unexpected field, Invalid date, Invalid email, Too many items, Malformed JSON | Broken structured request under `structure-v1` | Broken structured answer; finding and held preview |
| Exceeds numeric limit, Missing comparison value, Invalid number, Decimal over/within limit | Numeric request under `business-number-le-v1` | Different numeric answer; comparison result without coercion/rounding |
| Duplicate/Empty/Multiline sections | Labeled request under `completeness-v1` | Labeled answer; Short next steps also tests the output-only minimum |
| Business rule error | Request with reversed dates/excess refund, under `business-v1` | Different invalid refund/date values; business findings |
| Missing information | Required summary absent, under `completeness-v1` | Required next steps empty; completeness finding |

To test several output examples efficiently: choose **Clean question**, run input
checks once, then choose an output example and run output checks. Choose **Safe
reply** and rerun output to compare the clean case; the input test need not be
repeated. For focused JSON/completeness profiles, select the profile first, then
**Clean question** to prepare a valid input without touching the proposed answer.
For redaction, explicitly select `redaction-v1` before the Redaction preview button.

## How an application uses the API

1. Send the user's message to `POST /v1/checks/input`.
2. Apply your policy decision before calling your AI model.
3. Send the proposed AI answer to `POST /v1/checks/output`.
4. Apply that decision before displaying the answer.

Both endpoints take the same canonical content. Example request body:

```json
{
  "application_id": "demo",
  "policy_version": "secrets-extended-v1",
  "content": "password = \"synthetic-example!42\"",
  "content_format": "text"
}
```

Use `Authorization: Bearer YOUR_LOCAL_PLATFORM_KEY`. The Azure provider key, if
configured separately, is not the browser/platform key. `request_context` is not
scanned by the current local secret/PII/text checks; put material requiring a scan
in `content`. For `content_format: "json"`, put the JSON document as a string in
`content`. Unknown request fields are rejected. The local API explorer is at
http://127.0.0.1:8000/docs.

## Reading a result

| Result | Meaning |
| --- | --- |
| Passed | That selected checker found no supported signal; broader safety/privacy is not established |
| Finding | A supported pattern or configured rule matched; review the rule and your application context |
| Error / skipped / unavailable | The check did not complete or did not apply; a required check makes the evaluation incomplete |
| Simulated | Only orchestration was exercised; no real safety/bias assessment occurred |
| Disabled | No active checker for that group in this policy |
| Not applicable | A configured checker is scoped to the other stage |
| Not implemented | This product does not yet assess that group |

`recommended_action` is the policy's allow/flag/redact/block recommendation.
In **observe** mode, `effective_action` remains allow, even with findings. In
**enforce** mode it applies the recommendation. The dashboard holds the ordinary
answer preview for findings, failed checks, or incomplete coverage independently
of observe mode. It is a demonstration of review behavior, not a replacement for
your application's enforcement code.

Redaction is a separate preview/result. A clean ordinary preview does not certify
privacy, correctness, compliance, or absence of bias. Reports omit submitted and
redacted text; your fixture files themselves contain their saved input examples.

## Available validation groups

| Group | Current checks | Use case / boundary |
| --- | --- | --- |
| Regulated and sensitive data | Supported email, card and U.S. SSN candidates; opt-in labeled phone, birth date, medical-record, health-plan and bank-account fields; revalidated redaction | Remove supported identifiers before downstream processing; not general PHI or full anonymization |
| Secrets and confidential information | Selected provider credentials, JWT/private-key markers, embedded URL credentials; optional assignments, encoded inspection and confidential phrases | Catch credential/configuration leaks and customer-defined phrases; full chapter below |
| Prompt injection and manipulation | Selected hidden/control characters, explicit encodings; optional Azure input-attack analysis | Review obfuscated text; local text signals do not classify malicious intent |
| Harmful content and abuse | Safety simulator only | Real hate, violence, self-harm, sexual-content and abuse detection remains pending |
| Structure and data validity | Strict bounded JSON, JSON Schema and supported formats | Reject malformed data and schema/format violations; not factual verification |
| Scope and intent | Not implemented | A shopping bot receiving an unrelated question is not currently classified as off-topic |
| Accuracy and supporting evidence | Not implemented | A fabricated citation is not currently checked against evidence/databases |
| Bias and fairness | Bias simulator only | Real contextual fairness assessment and reviewed rubrics remain pending |
| Business rules and commitments | Numeric/date comparisons and labeled section completeness | Check submitted refund amounts/date order or required labels; not authoritative business truth or semantic/legal adequacy |
| Code and action safety | Not implemented | Shell/code/tool-call safety is not currently evaluated or sandboxed by this product |

## Secrets and confidential information — complete supported batch

### Choose the policy

| Policy | Enabled checks | Actions |
| --- | --- | --- |
| `secrets-v1` | Provider/JWT/private-key/URL patterns | Input recommends flag; output recommends block; observe mode allows |
| `secrets-extended-v1` | Same patterns plus quoted assignments, labeled encoding inspection and two fictional confidential-term rules | Same observe behavior; available in the dashboard |
| `secrets-extended-enforce-v1` | Extended checks | Enforces input flag / output block; included in the batch policy file |

`policies/secrets-batch.json` contains all three. The dashboard provides separate
input/output **Quoted password**, **Confidential term**, **Encoded credential**
and **Credential pattern** examples. These never change the profile. The normal
combined conversation profile enables the extended checks; Advanced can select a
focused profile. Edit the content and rerun to see findings disappear when corrected.
The group shows whether optional assignment/encoding checks are enabled and how
many confidential-term rules are active for the current stage.

### Credential-format checks

These are local **candidate signals**, not proof of a working or privately owned
credential. Some credential identifiers are public by design and may still need
review. No provider is contacted to verify any value. The examples below describe
formats/use cases; ellipses are illustrative, not executable test credentials.
Use the supplied dashboard sample or saved synthetic regression case to run them.

| Finding ID | What it checks | Example or use case | Expected result |
| --- | --- | --- | --- |
| `secrets.aws_credentials` | Selected AWS access-key identifiers and secret-key assignments | A pasted support log contains an AKIA-prefixed identifier | Finding; activity/secret ownership not verified |
| `secrets.github_token` | Classic GitHub token prefixes and fine-grained `github_pat_` candidates | An AI answer repeats a developer's GitHub token | Finding; prefix/shape, not token validity |
| `secrets.gitlab_token` | Selected GitLab access/deploy/runner/token prefixes | A CI configuration includes `glpat-…` | Finding |
| `secrets.openai_token` | The installed detector's supported OpenAI token shapes | A copied application configuration contains a supported model-provider key | Finding; not all future/vendor formats covered |
| `secrets.stripe_key` | Selected Stripe live-key shapes | A billing integration's key appears in an answer | Finding |
| `secrets.slack` | Selected Slack token and incoming webhook patterns | A message includes an `xoxb-…` token or Slack webhook URL | Finding |
| `secrets.sendgrid` | Selected SendGrid key shape | An email integration config contains an `SG.…` key | Finding |
| `secrets.twilio` | Selected Twilio credential identifier shapes | A pasted SMS configuration contains an SK/AC-prefixed identifier | Finding; an identifier alone is not proof of an active secret |
| `secrets.npm` | Registry auth-token assignment format | A `.npmrc` snippet includes a registry `:_authToken=` assignment | Finding; arbitrary bare npm-looking strings are outside this pattern |
| `secrets.pypi` | Selected PyPI/TestPyPI token shapes | A Python package publishing config contains a `pypi-…` token | Finding |
| `secrets.azure_storage` | Azure Storage `AccountKey=` candidate shape | A storage connection string is pasted into a prompt | Finding |
| `secrets.basic_auth` | URL containing embedded user/password components | `https://example-user:synthetic-pass@example.invalid/path` | Finding; no destination request is made |
| `secrets.discord` | The installed detector's supported Discord token shape | A bot configuration is included in a chat message | Finding; not every Discord format covered |
| `secrets.telegram` | Selected numeric-ID/bot-token shape | A Telegram bot config is pasted into an answer | Finding |
| `secrets.jwt` | Formally structured JWT candidates | A session token is included in a support transcript | Finding; signature, expiration, trust and ownership are not verified |
| `secrets.private_key` | Supported private-key headers/markers | `-----BEGIN RSA PRIVATE KEY-----` appears in text | Finding; key body/cryptographic validity not checked |

Private-key variants include DSA, EC, OpenSSH, PGP, PKCS#8-style PRIVATE KEY,
RSA, SSH2 ENCRYPTED PRIVATE KEY, and PuTTY format-2 markers. Pattern checks do
not establish whether a complete private key is present.

Each provider has input and output cases named
`provider.PROVIDER_NAME.input` / `.output` in `evals/secrets.json`. Base observe
cases expect input flag/output block recommendations with effective allow.
The matrix also tests JSON keys, decoded string values and nested arrays/objects,
enforce-mode actions, no source echo, and local-first external-transfer holding.

### Quoted credential assignments

Enable `detect_secret_assignments: true` in a policy containing a `secrets` rule.
This heuristic looks for simple quoted assignments to supported secret-named
fields: `password`, `passwd`, `pwd`, `api_key`, `auth_token`, `access_token`,
`refresh_token`, `client_secret`, `secret_key`, `secret`, `token`, with bounded underscore prefixes
such as `db_password`. JSON string-valued fields retain that key/value context.

| Example | What it demonstrates | Expected result in the extended policy |
| --- | --- | --- |
| `password = "synthetic-example!42"` | Hardcoded quoted password | `secrets.credential_assignment` |
| `db_password: "changeme"` | Weak-looking values are still literals | Finding |
| `client_secret = "x"` | Short values are not silently exempted | Finding |
| `{"outer":[{"db_password":"synthetic-value"}]}` | JSON nested string-field context | Finding with JSON format selected |
| `password = "${PASSWORD}"` | Environment-reference placeholder | No assignment finding |
| `password = "<PASSWORD>"` / `password = "YOUR_PASSWORD"` | Recognized documentation/reference placeholders | No assignment finding |
| `password = "{{ secrets.password }}"` | Template reference | No assignment finding |
| `password_length = "12"` | A similarly named metadata field | No assignment finding |
| `password = ""` | Empty literal | No assignment finding |

This is not general password recognition or a source-code parser. Unquoted
assignments, arbitrary prose/bearer headers, numeric JSON PINs, unsupported field
names and all programming-language escapes are not comprehensively handled.
Reference-like values can be real credentials; configured placeholder suppression
can therefore miss such a value. Supported literal values over 512 characters
produce an incomplete check (`secret_assignment_limit`), not a pass.

### Configured confidential phrases

A customer can list specific internal project names or phrases. Example:

```json
"confidential_terms": [
  {"rule_id":"internal_project","term":"PROJECT LANTERN"},
  {"rule_id":"unreleased_plan","term":"unreleased pricing plan","stages":["output"]}
]
```

The fictional sample `PROJECT LANTERN` triggers
`confidential.internal_project`. Matching uses Unicode NFKC normalization and
case folding on a scan copy; original content is unchanged. It is literal
substring matching, not regex, sentiment or intent analysis.

| Example | Expected result |
| --- | --- |
| `project lantern` | Finding under the sample rule |
| Full-width `ＰＲＯＪＥＣＴ ＬＡＮＴＥＲＮ` | Same finding after normalization |
| `This is not PROJECT LANTERN.` | Still a finding: negation does not exempt a literal match |
| `unreleased pricing plan` on input | No finding for the sample output-only rule |
| The same phrase on output | `confidential.unreleased_plan` |
| A proprietary document with no configured phrase | Not classified by these rules |

Terms require 3–200 characters, at most 50 rules, neutral unique rule IDs, and
active matching stages. Duplicate normalized terms in overlapping stages and
options without a secrets checker fail policy startup. Term values are absent
from metadata/findings/logs/reports; choose non-sensitive rule IDs because IDs
are visible. No terms are downloaded from a customer system automatically.

### Labeled Base64/hex inspection

Enable `inspect_encoded_secrets: true` to decode explicitly labeled `base64:` or
`hex:` tokens once as UTF-8, then apply the same provider, assignment and phrase
checks. Use **Encoded credential** to test a synthetic encoded GitHub candidate.
A harmless `base64: SGVsbG8=` or `hex: 68656c6c6f` contains no supported secret
and passes this secret-specific inspection. The separate text-security checker
can still flag benign encoding as a review signal when selected in another policy.

| Case | Expected result |
| --- | --- |
| Labeled encoding of a supported provider token | That provider's finding ID |
| Labeled encoding of a configured confidential phrase | That confidential rule's finding |
| Labeled encoding of a quoted credential assignment | Assignment finding when enabled |
| Invalid/empty/non-UTF-8 payload | Incomplete: `secret_encoding_invalid` |
| More than 16 payloads, token over 4,096 chars, or total over 8,192 token chars | Incomplete: `secret_encoding_limit` |
| A second encoding inside the decoded text | Not decoded again |

Budgets are shared across JSON units. There is no arbitrary unlabeled decoding,
recursive decoding, compressed-file unpacking, document/OCR scanning, or general
Unicode token-smuggling detection. The normal request/parser limits and a bounded
local scan deadline also apply. Errors never become a clean secret-check pass.

### Enforcement, privacy and external routing

- Base/extended observe policies recommend flag on input and block on output,
  while effective action stays allow. The enforce batch profile applies them.
- Secret findings do not return values, excerpts, hashes, or decoded content.
  Secrets are not automatically redacted; only the separate PII checker supports
  redaction. A secret redaction action is rejected at policy startup.
- JSON keys/string values are scanned; duplicate keys/invalid bounded JSON produce
  incomplete checks. Integers/booleans are not secret-token text candidates.
- User-supplied allowlist/noqa/gitleaks comments do not disable scanning.
- Configured local findings/errors hold external checks under the local-first
  gate, including in observe mode. This does not stop model calls your app makes
  independently of Valinator or prove complete confidentiality coverage.
- The optional Azure sample now enables quoted-assignment and encoded-secret
  inspection in its local gate. The ordinary launcher still disables Azure.

### Secrets batch case catalog

The complete case-by-case catalog follows. Every listed input is synthetic.
`evals/secrets.json` contains exact runnable input/expected metadata. Expanded
Python/API tests additionally cover the stage/mode/JSON/network/gating matrix;
they do not constitute independent detector accuracy calibration.

| Case ID | Example / use case | Expected result |
| --- | --- | --- |
| `provider.aws_credentials.input` | Pasted aws credentials candidate on input | secrets.aws_credentials; recommend flag |
| `provider.aws_credentials.output` | Pasted aws credentials candidate on output | secrets.aws_credentials; recommend block |
| `provider.github_token.input` | Pasted github token candidate on input | secrets.github_token; recommend flag |
| `provider.github_token.output` | Pasted github token candidate on output | secrets.github_token; recommend block |
| `provider.jwt.input` | Pasted jwt candidate on input | secrets.jwt; recommend flag |
| `provider.jwt.output` | Pasted jwt candidate on output | secrets.jwt; recommend block |
| `provider.openai_token.input` | Pasted openai token candidate on input | secrets.openai_token; recommend flag |
| `provider.openai_token.output` | Pasted openai token candidate on output | secrets.openai_token; recommend block |
| `provider.private_key.input` | Pasted private key candidate on input | secrets.private_key; recommend flag |
| `provider.private_key.output` | Pasted private key candidate on output | secrets.private_key; recommend block |
| `provider.stripe_key.input` | Pasted stripe key candidate on input | secrets.stripe_key; recommend flag |
| `provider.stripe_key.output` | Pasted stripe key candidate on output | secrets.stripe_key; recommend block |
| `provider.gitlab_token.input` | Pasted gitlab token candidate on input | secrets.gitlab_token; recommend flag |
| `provider.gitlab_token.output` | Pasted gitlab token candidate on output | secrets.gitlab_token; recommend block |
| `provider.slack.input` | Pasted slack candidate on input | secrets.slack; recommend flag |
| `provider.slack.output` | Pasted slack candidate on output | secrets.slack; recommend block |
| `provider.sendgrid.input` | Pasted sendgrid candidate on input | secrets.sendgrid; recommend flag |
| `provider.sendgrid.output` | Pasted sendgrid candidate on output | secrets.sendgrid; recommend block |
| `provider.twilio.input` | Pasted twilio candidate on input | secrets.twilio; recommend flag |
| `provider.twilio.output` | Pasted twilio candidate on output | secrets.twilio; recommend block |
| `provider.npm.input` | Pasted npm candidate on input | secrets.npm; recommend flag |
| `provider.npm.output` | Pasted npm candidate on output | secrets.npm; recommend block |
| `provider.pypi.input` | Pasted pypi candidate on input | secrets.pypi; recommend flag |
| `provider.pypi.output` | Pasted pypi candidate on output | secrets.pypi; recommend block |
| `provider.azure_storage.input` | Pasted azure storage candidate on input | secrets.azure_storage; recommend flag |
| `provider.azure_storage.output` | Pasted azure storage candidate on output | secrets.azure_storage; recommend block |
| `provider.basic_auth.input` | Pasted basic auth candidate on input | secrets.basic_auth; recommend flag |
| `provider.basic_auth.output` | Pasted basic auth candidate on output | secrets.basic_auth; recommend block |
| `provider.discord.input` | Pasted discord candidate on input | secrets.discord; recommend flag |
| `provider.discord.output` | Pasted discord candidate on output | secrets.discord; recommend block |
| `provider.telegram.input` | Pasted telegram candidate on input | secrets.telegram; recommend flag |
| `provider.telegram.output` | Pasted telegram candidate on output | secrets.telegram; recommend block |
| `benign.1` | Benign/reference lookalike: `Explain key rotation.` | No supported finding; allow |
| `benign.2` | Benign/reference lookalike: `api_key = ${API_KEY}` | No supported finding; allow |
| `benign.3` | Benign/reference lookalike: `password = "${PASSWORD}"` | No supported finding; allow |
| `benign.4` | Benign/reference lookalike: `password = "<PASSWORD>"` | No supported finding; allow |
| `benign.5` | Benign/reference lookalike: `password = "YOUR_PASSWORD"` | No supported finding; allow |
| `benign.6` | Benign/reference lookalike: `password = "{{ secrets.password }}"` | No supported finding; allow |
| `benign.7` | Benign/reference lookalike: `password = ""` | No supported finding; allow |
| `benign.8` | Benign/reference lookalike: `password_length = "12"` | No supported finding; allow |
| `benign.9` | Benign/reference lookalike: `No credentials are included.` | No supported finding; allow |
| `benign.10` | Benign/reference lookalike: `-----BEGIN PUBLIC KEY-----` | No supported finding; allow |
| `benign.11` | Benign/reference lookalike: `ghp_short` | No supported finding; allow |
| `benign.12` | Benign/reference lookalike: `github_pat_example` | No supported finding; allow |
| `benign.13` | Benign/reference lookalike: `glpat-short` | No supported finding; allow |
| `benign.14` | Benign/reference lookalike: `xoxb-short` | No supported finding; allow |
| `benign.15` | Benign/reference lookalike: `SG.short.short` | No supported finding; allow |
| `benign.16` | Benign/reference lookalike: `AccountKey=short` | No supported finding; allow |
| `benign.17` | Benign/reference lookalike: `npm install package` | No supported finding; allow |
| `benign.18` | Benign/reference lookalike: `https://example.invalid/path` | No supported finding; allow |
| `benign.19` | Benign/reference lookalike: `pypi-short` | No supported finding; allow |
| `benign.20` | Benign/reference lookalike: `SKshort` | No supported finding; allow |
| `benign.21` | Benign/reference lookalike: `12345:short` | No supported finding; allow |
| `benign.22` | Benign/reference lookalike: `public_key = "abc"` | No supported finding; allow |
| `benign.23` | Benign/reference lookalike: `jane@example.com` | No supported finding; allow |
| `benign.24` | Benign/reference lookalike: `Order 219099999` | No supported finding; allow |
| `assignment.1` | Quoted credential assignment | secrets.credential_assignment; recommend flag |
| `assignment.2` | Quoted credential assignment | secrets.credential_assignment; recommend flag |
| `assignment.3` | Quoted credential assignment | secrets.credential_assignment; recommend flag |
| `assignment.4` | Quoted credential assignment | secrets.credential_assignment; recommend flag |
| `assignment.5` | Quoted credential assignment | secrets.credential_assignment; recommend flag |
| `assignment.json.1` | Quoted credential assignment in nested JSON | secrets.credential_assignment; recommend flag |
| `assignment.json.2` | Quoted credential assignment in nested JSON | secrets.credential_assignment; recommend flag |
| `confidential.1` | Configured phrase / stage scope / unrelated-document check | confidential.internal_project; recommend flag |
| `confidential.2` | Configured phrase / stage scope / unrelated-document check | confidential.internal_project; recommend flag |
| `confidential.3` | Configured phrase / stage scope / unrelated-document check | confidential.internal_project; recommend flag |
| `confidential.4` | Configured phrase / stage scope / unrelated-document check | confidential.internal_project; recommend flag |
| `confidential.input-scope` | Configured phrase / stage scope / unrelated-document check | No supported finding; allow |
| `confidential.output-scope` | Configured phrase / stage scope / unrelated-document check | confidential.unreleased_plan; recommend block |
| `confidential.unconfigured-document` | Configured phrase / stage scope / unrelated-document check | No supported finding; allow |
| `encoding.github_token.base64` | Single-layer labeled encoding inspection: github_token / base64 | secrets.github_token; recommend flag |
| `encoding.github_token.hex` | Single-layer labeled encoding inspection: github_token / hex | secrets.github_token; recommend flag |
| `encoding.slack.base64` | Single-layer labeled encoding inspection: slack / base64 | secrets.slack; recommend flag |
| `encoding.slack.hex` | Single-layer labeled encoding inspection: slack / hex | secrets.slack; recommend flag |
| `encoding.private_key.base64` | Single-layer labeled encoding inspection: private_key / base64 | secrets.private_key; recommend flag |
| `encoding.private_key.hex` | Single-layer labeled encoding inspection: private_key / hex | secrets.private_key; recommend flag |
| `encoding.basic_auth.base64` | Single-layer labeled encoding inspection: basic_auth / base64 | secrets.basic_auth; recommend flag |
| `encoding.basic_auth.hex` | Single-layer labeled encoding inspection: basic_auth / hex | secrets.basic_auth; recommend flag |
| `encoding.benign-base64` | Single-layer labeled encoding inspection: benign-base64 | No supported finding; allow |
| `encoding.benign-hex` | Single-layer labeled encoding inspection: benign-hex | No supported finding; allow |
| `encoding.no-recursion` | Single-layer labeled encoding inspection: no-recursion | No supported finding; allow |
| `failure.1` | Invalid Base64 symbols | Incomplete; block recommendation |
| `failure.2` | Hex bytes are not UTF-8 text | Incomplete; block recommendation |
| `failure.3` | Empty encoding marker | Incomplete; block recommendation |
| `failure.4` | 17 labeled payloads exceed the count budget | Incomplete; block recommendation |
| `failure.5` | A 4,097-character token exceeds the individual limit | Incomplete; block recommendation |
| `failure.6` | 12,288 encoded characters exceed the total limit | Incomplete; block recommendation |
| `failure.7` | Quoted literal exceeds 512 characters | Incomplete; block recommendation |
| `failure.duplicate-json` | Duplicate JSON object keys | Incomplete; block recommendation |
| `enforce.input` | Enforced input action for supported candidate/term | secrets.github_token; recommend flag; effective flag |
| `enforce.output` | Enforced output action for supported candidate/term | confidential.internal_project; recommend block; effective block |
| `disabled.assignments` | Optional assignments behavior disabled in base policy | No supported finding; allow |
| `disabled.terms` | Optional terms behavior disabled in base policy | No supported finding; allow |
| `boundary.encoded-count` | Exactly 16 benign encoded payloads within the shared budget | No supported finding; allow |

| `variant.github_fine.input` | github fine candidate on input | `secrets.github_token`; recommend flag |
| `variant.github_fine.output` | github fine candidate on output | `secrets.github_token`; recommend block |
| `variant.openai_project.input` | openai project candidate on input | `secrets.openai_token`; recommend flag |
| `variant.openai_project.output` | openai project candidate on output | `secrets.openai_token`; recommend block |
| `variant.private_ec.input` | private ec candidate on input | `secrets.private_key`; recommend flag |
| `variant.private_pgp.output` | private pgp candidate on output | `secrets.private_key`; recommend block |

## Regulated and sensitive data checks

The normal conversation profile now enables the expanded local privacy checks on
both stages. In Advanced, `pii-extended-v1` focuses on this group;
`redaction-v1` demonstrates supported replacements after rechecking. The older
`pii-v1` keeps its original email/card/SSN scope.

| Check / finding | Example or use case | What is checked / expected result |
| --- | --- | --- |
| `pii.email_address` | `Contact jane@example.com.` in a support answer | Presidio pattern and bundled domain-suffix check; finding, no mailbox verification |
| `pii.credit_card` | Synthetic card `4111 1111 1111 1111` | Presidio candidate pattern and Luhn checksum; finding, no account verification |
| `pii.us_ssn` | Synthetic-format `SSN: 219-09-9999` | Selected U.S. SSN candidate; context and invalid-value exclusions, no issuance verification |
| `pii.phone_number` | `Phone: 202-555-0147` in a support form | Explicit label plus bounded ASCII phone syntax; finding, no ownership/reachability lookup |
| `pii.date_of_birth` | `DOB: 1980-02-29` in intake data | Explicit label plus real calendar date in supported format; finding, no identity or actual-birth check |
| `pii.medical_record_number` | `MRN: A1234` in a clinical export | Explicit medical-record label plus identifier shape; finding, no medical-system lookup |
| `pii.health_plan_id` | `insurance_member_id: M000123` | Explicit member/health-plan label plus identifier shape; finding, no membership verification |
| `pii.bank_account` | `bank_account_number: 76543210` | Explicit account label plus 6–17 ASCII digits; finding, no bank/account verification |
| Invalid/lookalike values | `DOB: 2025-02-29`, `MRN: ABC`, `Order reference 202-555-0147` | No new-field finding; not a declaration of privacy or schema validity |
| Supported redaction | `Phone: 202-555-0147` under `redaction-v1` | Separate `Phone: [REDACTED_PHONE_NUMBER]` preview after selected checks rerun |
| No supported identifier | Clean text under a redaction policy | `not_needed`; no transformed text returned |
| Sensitive JSON key / invalid transformed content | A sensitive value in a key, or numeric replacement violates schema | Redaction withheld; no original/transformed text released |
| Field scan limit | 65 recognized labeled fields or one value over 128 characters | `pii_field_limit`; required check incomplete, never a partial pass |

### Configure labeled fields

Set `detect_labeled_sensitive_data: true` in a trusted policy containing a `pii`
rule. It is strict Boolean, defaults off, and cannot be enabled without that rule.
The installed Presidio email/card/SSN adapter is still required; the extension
adds bounded local field heuristics, no model, dependency or network call.

Supported labels are case-insensitive; spaces and hyphens normalize to underscores:

| Type | Exact supported label names | Value shape |
| --- | --- | --- |
| Phone | `phone`, `phone_number`, `telephone` | Unprefixed: 10 ASCII digits, first and fourth digits 2–9. Leading `+`: 8–15 digits, first digit nonzero. Spaces, dots, parentheses and hyphens allowed, at most 32 characters; no numbering-plan validation or extensions |
| Birth date | `dob`, `date_of_birth`, `birth_date` | Exactly `YYYY-MM-DD` or U.S. `MM/DD/YYYY`, valid calendar date, year 1900–2099; future dates in that interval also flag as candidates |
| Medical record | `mrn`, `medical_record_number` | 4–32 ASCII alphanumeric/hyphen characters, starts alphanumeric and contains at least one digit |
| Health plan | `member_id`, `health_plan_id`, `insurance_member_id` | Same identifier shape, 5–32 characters; generic `member_id` can produce false alarms outside healthcare |
| Bank account | `bank_account`, `bank_account_number`, `account_number` | 6–17 ASCII digits; generic `account_number` can be an unrelated application ID |

Plain text requires **one complete labeled value per line**, with `:` or `=`.
Indentation, surrounding spaces and one pair of matching single/double quotes
are supported. Arbitrary prose, comma/semicolon-separated fields, code syntax,
JSON-looking text sent as text, Unicode digits, unsupported aliases, encoded
values, Markdown labels and other date/identifier formats can be missed.
This detection is distinct from validating whether a form field is valid: a
non-matching date or number can still be sensitive and may require your schema.

In JSON mode, exact decoded keys label string and integer values, including
scalar lists under the key. Nested dictionaries use their own keys; a parent
`phone` object does not automatically label all descendants. Booleans and floats
are not treated as identifiers. Large integer formats can lose leading zeros;
send identifiers as strings. Keys are scanned but never renamed, to avoid
collisions. Recognized labeled-field attempts share a **64-field budget across
the document**, including empty/invalid candidates. A trimmed value longer than
128 characters fails before inspection completes. These bounds share the PII
adapter's 0.2-second local deadline and normal policy deadline.

Neither `request_context` nor files/images are scanned. Names, postal addresses,
diagnoses, free-text medical narratives, other identifiers, consent and legal
compliance remain outside this batch. Identifying a field does not establish its
PHI status or provide complete anonymization. Unrelated content remains visible
in a redacted preview; review what your application permits downstream.

### Run the entire privacy group

```powershell
py -3.13 start_dashboard.py --evaluate --suite privacy
```

This runs **100 expectations** covering input/output, positive candidates,
benign lookalikes, explicitly unsupported formats, nested/escaped/numeric JSON,
observe/enforce behavior, revalidated redaction and failure limits. **12 cases
intentionally expect incomplete checks** (malformed JSON or exceeded scan limits).
A matched expectation does not turn an incomplete check into a privacy pass.
Results are offline and not an independently measured accuracy/recall benchmark.

See [all 100 cases and their examples](privacy-coverage.md). Fixtures are in
`evals/privacy.json`; profiles in `policies/privacy-batch.json`. Separate API,
engine and browser tests cover sensitive-key withholding, schema-breaking
redaction, external-call gating, no-network behavior and value-free reports.
The dashboard offers Phone number, Birth date, Medical record ID, Insurance member
ID and Bank account in both example sets. Input examples demonstrate the input
gate; output examples demonstrate answer findings. Under `redaction-v1`,
Redaction preview covers all eight supported types on either stage. No
paid/provider call is made.

## Prompt injection and manipulation checks

| Check | Example or use case | Expected behavior |
| --- | --- | --- |
| Selected hidden controls | `hidden` + zero-width space + `character` | `text_security.hidden_controls`; no scrubbing |
| Legitimate scripts/emoji joiners | Normal multilingual text or joined emoji | Joiners alone are not flagged by this limited check |
| Labeled encoding review | `base64: SGVsbG8=` | `text_security.encoded_text`, even though benign |
| Encoded hidden controls | A labeled payload containing a selected control | `text_security.encoded_hidden_controls` |
| Invalid labeled encoding | `hex: zz` | `text_security.invalid_encoding` review finding |
| Inspection budget exceeded | More than 16 labeled tokens | Error/incomplete; no pass |
| Optional Azure Prompt Shields | User input designed to override instructions | If explicitly configured and local gates pass, Azure returns a possible-attack signal |

Text-security signals do not establish malicious intent. No multi-turn, image,
all-Unicode, general jailbreak or output-attack guarantee exists. Azure is off by
default, input-only, limited to 4,000 plain-text characters, and not yet live
calibrated here. Original input leaves for the configured Azure resource only
after configured real local checks pass and policy permission is granted. See
README for provider credentials, charges, region/retention review and failures.

## Structure and data validity checks

This group checks whether data follows your application's contract. It does not
establish truth, privacy, safety or fairness. The dedicated **`structure-v1`**
profile contains only the real local schema checker; other groups remain visibly
disabled. The normal free-text conversation profile is unchanged.

### Try the structured demo

1. Under **Advanced**, select `structure-v1`.
2. In Step 1 choose **Clean question**, which loads a valid JSON request, then
   **Run input checks**.
3. In Step 3 choose **Missing field**, **Wrong data type**, **Unexpected field**,
   **Invalid date**, **Invalid email**, **Too many items** or **Malformed JSON**.
4. Run output checks. The finding holds the answer. Choose **Safe reply** and
   rerun output to see the passing case without repeating input.

The same error examples are available for input. Each button changes only its
own editor and format; it never changes the other editor or the profile. There
are now 36 input / 39 output examples across all groups. Only examples for the active profile are shown; select another profile under Advanced
to explore other groups.

### Sample contract and checks

These sample requirements are educational. Applications provide their own trusted
`input_schema` and `output_schema` in a policy with a `schema` rule.

| Check | Example / use case | Expected behavior |
| --- | --- | --- |
| Root type and extra fields | A root array/null, or unexpected property | `schema.invalid`; the sample requires an object with no extras |
| Required fields | Missing input `message` or output `answer` | Finding; request and reply use distinct contracts |
| String type and length | Numeric content, `Hi`, or 501 characters | Finding; content must be a string of 5–500 characters |
| UUID | `request_id: "not-a-uuid"` | Finding; valid UUID syntax is not identity/ownership verification |
| Allowed choices | Input priority outside low/normal/high; output status outside ok/review | Finding |
| Local references and nested objects | Contact missing email or containing an extra property | Finding through local `$defs`; no remote reference download |
| Email syntax | `contact.email: "not-an-email"` | Finding; no DNS/mailbox/ownership lookup |
| Array size and uniqueness | Empty items, four items, or duplicate SKU/quantity objects | Finding; 1–3 unique objects required |
| Item shape | SKU `abc-123`, missing quantity, Boolean quantity | Finding; SKU uses `ABC-123` shape and quantity must be an integer |
| Numeric bounds | Quantity 0 or 11 | Finding; permitted range is 1–10 |
| Integer multiples | Sequence 7 | Finding; sequence must be 0–100 and a multiple of 5 |
| Date-time | `metadata.created_at: "2026-10-28T12:30:00Z"` | Passes this format check; event occurrence is not verified |
| Date | `metadata.due_date: "2026-02-30"` | Finding; valid example `2026-10-30` |
| Time | `metadata.notify_at: "12:30:00Z"` | Passes this format check; bare `12:30:00` now fails |
| IPv4 / IPv6 | `999.1.2.3` / `not-an-ipv6` | Finding; valid examples `192.0.2.1` / `2001:db8::1`; no network/ownership check |
| Explicit nullability | Optional note is null or a string of at most 40 characters | Passes; missing required fields still fail |
| Strict JSON | Truncation, comments, trailing comma, single quotes, duplicate decoded keys, NaN/Infinity, overflow or unpaired surrogate | `schema.invalid_json`; no parser exception text returned |
| Parser budgets | 33 nested containers, 1,001 entries or a 129-character numeric token | `schema.invalid_json`; exceeds supported strict-JSON limits |
| Required schema skipped | Text mode selected, or stage schema missing | Skipped and incomplete; not assessed or passed |
| Optional schema skipped | Text mode under an optional schema rule | Engine can report complete/allow, but checker is skipped and dashboard holds input; not assessed or passed |

The sample also requires `request_id`, priority/status, contact, items, metadata
and sequence on both stages. Metadata requires created_at, due_date, notify_at,
ip_v4 and ip_v6 with no extras. Items require sku/quantity with no extras. The
profiles and full schemas are in `policies/structure-batch.json`.

### Explanations and format compatibility

Schema checker version **2** preserves `schema.invalid` and `schema.invalid_json`.
The first schema problem gets a fixed generic reason, such as “A required field
is missing” or “A value has the wrong data type.” Other keywords can use a generic
fallback. This is not an exhaustive error list. Submitted values, field names,
JSON paths, expected values, schema text and library exception messages are never
included; consult your configured schema to locate the problem.

**Compatibility change in 0.18.0:** time now uses the RFC3339 full-time format of
JSON Schema 2020-12, requiring `Z` or an explicit numeric offset. The prior generic
checker accidentally selected the older timezone-free rule. Send `12:30:00Z` or
`12:30:00-04:00` instead of bare `12:30:00`. Valinator does not guess a timezone.
Fractional seconds and lowercase `z` are accepted. Time and date-time reject
leading/trailing whitespace, including a terminal newline. The existing RFC3339
helper does not support leap seconds; `23:59:60Z` is outside the supported subset.
Primary format reference: [JSON Schema 2020-12, section 7.3.1](https://json-schema.org/draft/2020-12/json-schema-validation).

Only date, date-time, time, email, IPv4, IPv6 and UUID format names are supported;
unknown configured formats fail startup. No new dependency, model or provider
call is introduced. Schemas may reference local definitions, not remote URLs.

### Run the entire structure group

```powershell
py -3.13 start_dashboard.py --evaluate --suite structure
```

This runs **100 expectations**: 50 scenarios on both stages, covering valid
boundaries, all seven formats, nested/local-reference constraints, strict JSON,
observe/enforce decisions and required/optional skips. **Four cases deliberately
expect incomplete evaluations**, and **two expect optional skips**. Matching
those expectations does not make them completed data assessments. Reports omit
content and can compare to a previous report of the same suite ID. See [all 100
cases and examples](structure-coverage.md). Direct CLI use needs no PII extra:

```bash
python -m valinator.evaluate --cases evals/structure.json --policies policies/structure-batch.json
```

### Limits

Canonical content is at most 32,000 characters. The shared parser additionally
caps UTF-8 size at 256 KiB, nesting at 32 containers, object/array entries at 1,000
per container, and numeric tokens at 128 characters. Strings containing escaped
quotes/braces are handled as string data. Duplicate decoded keys and nonfinite
numbers are rejected.

Schema patterns are trusted application configuration and use the installed
validator's regex implementation. API deadlines are not hard CPU isolation for
synchronous validation; keep patterns simple. JSON numbers use Python number
handling, not exact decimal currency arithmetic. Integer `multipleOf` examples
do not establish decimal `multipleOf` accuracy. The comparison checker separately
preserves decimal tokens; schema arithmetic remains distinct. Use domain-specific
decimal logic for money. This suite is not exhaustive JSON Schema conformance testing, factual
verification, semantic business review, privacy filtering, repair/retry logic or
prompt-injection detection. A valid email intentionally remains data in this
schema-only profile; add privacy rules separately when your application needs them.

## Business rules and commitments checks

This group checks submitted numbers/dates and required labeled sections against
your configuration. It does not verify business truth, legal promises, clinical
adequacy, source evidence or whether a commitment is permitted by your systems.
Both comparison operands come from the submitted content: an attacker can invent
a purchase amount or limit. Your application must obtain authoritative limits
from a trusted source and prevent untrusted overrides. These are consistency
checks, not a fraud detector or an authoritative spending control.

### Choose a focused demo

| Profile | What runs | How to try it |
| --- | --- | --- |
| `business-v1` | JSON Schema plus date ordering and refund amount comparison | Choose Clean question and run input, then Refund too large, Reversed dates or Business rule error in output |
| `business-number-le-v1` | Numeric left <= right comparison only | Choose Clean question and run input, then numeric/decimal examples in output |
| `completeness-v1` | Required input Summary; required output Summary and Next steps | Choose Clean question and run input, then missing, empty, duplicate, short or multiline output examples |

Use **Advanced** to select a profile explicitly. Example buttons never switch it
or overwrite the opposite editor. Output examples preserve a completed input
check. Only examples applicable to the active profile are shown, to keep the
expanding catalogs manageable. There are 36 input / 39 output examples across
all groups. No AI answer is generated or provider called.

### Submitted-field comparisons

| Check / use case | Example | Expected behavior |
| --- | --- | --- |
| Less than (`lt`) | start_date 2026-10-01 vs end_date 2026-10-03 | Pass; equal dates fail this strict comparison |
| Less than or equal (`le`) | refund_amount 100 vs purchase_amount 100 | Pass; refund 150 vs purchase 100 flags `business.refund_limit` in the refund profile |
| Equal (`eq`) | left 2 vs right 2 | Pass; unequal numbers/dates flag |
| Greater than or equal (`ge`) | left 2 vs right 1 | Pass; equality also passes |
| Greater than (`gt`) | left 2 vs right 1 | Pass; equality fails |
| Different from (`ne`) | left 1 vs right 2 | Pass; equality fails; numeric -0 and 0 compare equal |
| Exact decimal comparison | left 1.0000000000000001 vs right 1.0 under `le` | Finding; the extra fraction must not disappear through float rounding |
| Tiny decimal | 1e-400 vs 0 under `eq` | Finding; nonzero token is not rounded to zero before comparison |
| Wrong type / missing value | left is true, null, numeric string, array/object, or a required path is absent | Finding; no string coercion or default-zero value |
| ISO calendar date | 2024-02-29 before 2024-03-01 | Pass; invalid day, datetime, non-ISO date or null flags |
| JSON Pointer | `/orders/0/a~1b` vs `/c~0d` | Resolve array index 0, key `a/b` and key `c~d`; missing/out-of-range/noncanonical array indices flag |
| Invalid bounded JSON | Duplicate decoded key, NaN, overflow or truncated input | `business.invalid_json`; check completed and found invalid input |
| Wrong content format / no active comparisons | Plain text selected for a required comparison rule | Skipped and incomplete, not passed |

Rules configure `rule_id`, left/right JSON Pointers, one of the six operators,
`value_type: number` or `date`, and optional stages. Paths are data traversal,
not code, formulas, attributes, URLs or expressions. `business.<rule_id>` identifies
the failed rule. A missing/invalid operand is now distinguished from a failed
relationship in the explanation. Actual operands and paths are never copied into
findings. Rule IDs are public configuration metadata; do not put secrets in them.

**Precision correction in 0.19.0:** comparison checker version 2 first validates
the canonical text with the existing strict JSON guards, then decodes those same
number tokens using Python Decimal. This prevents binary-float rounding before
comparison, including near-equality and tiny-exponent examples. If your client
already rounded a number before serializing it, the server cannot recover the
lost digits; send the intended numeric text. No dependency
is added. Integers remain exact, numeric strings and booleans are rejected, and
comparison does not round to the ambient Decimal arithmetic precision.

The original JSON limits still apply, including overflow rejection by the strict
parser. An explicitly encoded decimal exponent with absolute value above 1,000,000
also flags invalid JSON for this checker, consistently across Python versions.
An unrepresentable decimal is rejected too. This is comparison, not arithmetic: no totals,
rounding policy, currency conversions or external account/price lookup is done.
Schema parsing and JSON transformations retain their existing numeric handling;
this change does not promise exact decimal handling throughout every component.
The numeric-only profile allows negative numbers and extra fields if the
relationship passes; add a schema when your application requires nonnegative
amounts, field lists or other constraints.

### Required information / completeness

| Check / use case | Example | Expected behavior |
| --- | --- | --- |
| Missing section | Input has Details but no Summary | `completeness.request_summary`; explanation says missing |
| Empty section | `Summary:` followed only by whitespace | Too short finding, not a missing-label finding |
| Repeated section | `Summary: First` and `SUMMARY: Second` | Repeated finding; no convenient occurrence is selected |
| Output length threshold | `Next steps: Hi` | `completeness.next_steps`; fewer than five non-whitespace characters |
| Multiline body | `Summary:` followed by a body on later lines | Counts until the next recognized label |
| Unknown label boundary | Empty Summary followed by `Other: text` | Other text does not satisfy the empty Summary |
| Stage-specific requirements | Input `Summary: Hi` | Passes its one-character minimum; output still requires Next steps and its five-character minimum |
| Wrong format / no active sections | JSON submitted to a required text completeness rule | Skipped and incomplete, not assessed or passed |

Rules configure a public `rule_id`, ASCII label, minimum non-whitespace character
count and stages. Labels start at the beginning of a line, are case-insensitive,
and use a colon. Required labels must occur exactly once. Plain/multiline bodies
are supported; every recognized label, even an unknown one, ends the previous
body. Markdown headings, indentation, arbitrary prose and semantic adequacy are
not recognized. Meaningless text can satisfy length; invisible non-whitespace
characters can count too. Add other checks as needed. This is not a model-based
assessment of whether a section is useful, complete in meaning or correct.
Completeness checker version 2 now explains missing, repeated and too-short
conditions separately without copying the body.

### Decisions, examples and the full batch

Observe profiles recommend flag on input and block on output, while effective
action remains allow. The enforce regression profile applies that recommendation.
The dashboard holds findings regardless of observe mode. Required skip/error
follows failure_action (block by default). Passing only this group does not mean
privacy, prompt security, factual correctness or fairness was assessed.

New stage-specific examples include Exceeds numeric limit, Missing comparison
value, Invalid number, Decimal over limit, Decimal within limit, Refund too
large, Reversed dates, Duplicate section, Empty section and Multiline sections.
Short next steps is output-only. Precision examples remain raw text in the
browser so JavaScript does not round them before the API receives them.

```powershell
py -3.13 start_dashboard.py --evaluate --suite business
```

Runs **100 expectations**, 50 scenarios on both stages: all six operators,
precision/underflow boundaries, type/missing data, dates, pointers, invalid JSON,
observe/enforce actions and required labeled sections. **Six intentionally expect
incomplete evaluations** from required checks being skipped. A matched expected
skip is not a completed business assessment. [All 100 cases](business-coverage.md)
show each use case and expected result. Fixtures and profiles live in
`evals/business.json` and `policies/business-batch.json`. Reports omit content and
support comparison with a previous report of the same suite ID. No PII extra is
needed for direct CLI use:

```bash
python -m valinator.evaluate --cases evals/business.json --policies policies/business-batch.json
```

## Harmful content, bias and planned checks

The `safety` and `bias` adapters are explicitly simulated. For a manually loaded
support policy, `[SIMULATE:safety:FLAG]` exercises the safety finding path;
`[SIMULATE:bias:FLAG]` exercises the bias path. ERROR/TIMEOUT variants exercise
failure handling. These markers do not classify real content. An ordinary text
receiving a simulated pass has not received a safety or fairness assessment.

Scope/intent, factual accuracy/evidence, and code/tool-action safety remain
unimplemented. Image/document quality, CSAM/deepfake detection, malware sandboxing,
legal/medical judgments and citation verification are not silently covered by
another group. The dashboard labels unsupported groups rather than showing them
as passing. These require separate designs/providers and reviewed test data.

## Operating limits and test-report interpretation

The default process allows sustained 120 checks/minute per application with burst
20; 4 active checks per application, 16 across the process and 32 POST ingress
requests. Input/output share the application allowance and a dashboard run uses
two checks. Overload returns 429/503 with Retry-After, never a validation pass.
Settings are process-local and reset on restart; they are not shared billing or
model-token caps. Health/metadata remain outside those check quotas.

Regression reports show expectation matches separately from underlying findings,
incomplete evaluations and simulation. The Secrets suite deliberately expects
8 incomplete cases. A green match is not a clean-content claim or accuracy score.
Baseline comparisons show added/removed/changed cases and timing separately;
source/expectation equality is not verified by matching IDs. Preserve stable IDs
and change the suite ID when fixture definitions change. Reports are local and
standalone, omit input/redacted text and have no script/network dependencies.

See [README](../README.md) for commands/configuration, [specification](specification.md)
for contracts, and [backlog](backlog.md) for future work. New check behavior must
update this guide and its runnable examples as part of the same group batch.

### How synthetic examples are stored

Credential-positive regression entries use an explicit `synthetic_fixture` name
and `content: "[SYNTHETIC FIXTURE]"`. The runner constructs the known inert example
in memory only when executing that case. This keeps complete credential-shaped
strings out of committed fixture data. The fixed fixture registry cannot execute
code, select imports/files/URLs, or create/verify real provider credentials.
Unknown names, non-placeholder content or incompatible formats are rejected.
There is no expansion in the normal check API: only the offline regression runner
understands these names. Reports still omit constructed values. The case catalog
above describes each resulting use case; ordinary custom text cases remain
supported without a fixture name.
