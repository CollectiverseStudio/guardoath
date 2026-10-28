# Valinator user guide

This guide grows with the product. It describes user-facing validation checks,
examples, expected results, configuration, and boundaries. Version: 0.15.0.

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
| Regulated and sensitive data | Supported email, card and U.S. SSN candidates; optional revalidated redaction | Remove supported identifiers before downstream processing; not general PHI or full anonymization |
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

`policies/secrets-batch.json` contains all three. The dashboard's **Quoted
password**, **Confidential term**, and **Encoded credential** examples select the
extended observe policy automatically. **Credential pattern** selects the base
policy. Edit the content and rerun to see findings disappear when corrected.
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

Select `pii-v1` for local checks or `redaction-v1` for supported redaction.

| Check / finding | Example or use case | Expected behavior |
| --- | --- | --- |
| `pii.email_address` | `Contact jane@example.com.` in a support answer | Candidate finding; no mailbox/ownership verification |
| `pii.credit_card` | Synthetic Luhn-valid card `4111 1111 1111 1111` | Candidate finding; no card/account verification |
| `pii.us_ssn` | Synthetic-format `Example SSN: 219-09-9999` | Selected U.S. SSN candidate; identifier not verified |
| SSN context/invalid lookalikes | `Order 219099999` or `SSN: 000-09-9999` | No supported SSN finding under current rules |
| PII redaction | Contact email under `redaction-v1` | Separate masked preview after selected checks rerun |
| No supported identifier | Clean text under redaction policy | `not_needed`; no transformed text returned |
| Unsafe/invalid transformed content | Redaction would violate a selected schema/check | Redaction withheld; original answer remains held |

No general PHI, names, addresses, consent, location-law, biometric, or compliance
assessment is implemented. JSON field/key handling and stage actions matter; see
the detailed README for supported redaction types and limitations.

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

| Check | Example or use case | Expected behavior |
| --- | --- | --- |
| Strict bounded JSON | Duplicate keys, NaN or malformed JSON | Invalid JSON finding/error as appropriate; not accepted silently |
| Required fields/types | A business object missing `purchase_amount` | `schema.invalid` |
| Date format | `2026-02-30` in a date field | `schema.invalid` |
| Date-time/time | A configured field violates supported RFC-style format | Schema finding |
| Email format | A configured email field has invalid syntax | Schema finding; no DNS/mailbox lookup |
| IPv4/IPv6/UUID | Malformed value in a configured format field | Schema finding |
| Unknown schema format | Policy requests an unsupported format | Policy startup fails |
| Wrong content format | Required schema check receives text | Skipped/incomplete; not a validated structure |

Schemas define the boundaries. Passing JSON structure does not verify the facts,
URLs, financial/legal correctness, or safety of the content. Remote schema
references are unsupported.

## Business rules and commitments checks

| Check | Example or use case | Expected behavior |
| --- | --- | --- |
| Numeric comparison | Refund 150 exceeds submitted purchase 100 | `business.refund_limit`; output recommends block in sample policy |
| Date comparison | End date precedes start date | `business.date_order` |
| Missing/invalid comparison values | Absent or wrongly typed JSON Pointer field | Comparison finding, not silent success |
| Input summary presence | `Summary: Return request.` | Completeness passes for the input sample |
| Required next steps | `Summary: Ready.` followed by empty `Next steps:` | `completeness.next_steps` |
| Duplicate required label | Two `Summary:` sections | Completeness finding for ambiguous repetition |
| Complete labeled answer | Nonempty `Summary:` and sufficiently long `Next steps:` | Structural completeness passes |

Comparisons support lt/le/eq/ge/gt/ne for numbers/ISO dates, without expressions
or eval. Submitted purchase amounts are not checked against an authoritative
business system. Completeness recognizes explicit line-start `Label:` text only;
Markdown headings, semantic adequacy, legal clauses, medical sufficiency and truth
are not assessed. False text can satisfy a presence rule.

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
