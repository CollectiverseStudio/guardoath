# Valinator implementation specification

Version 0.1, October 27, 2026. This repository document captures the initial
engineering baseline. The separate product specification covers the wider plan.

## Purpose

Check AI inputs and outputs against application-specific, customer-approved
policies. Reuse existing checkers behind interchangeable adapters. Add consistent
decisions, contextual bias rubrics, reviewer workflows, and regression evidence.

## Initial scope

- English text for customer-support applications.
- Input and output API endpoints, with application-scoped credentials.
- Required-field, type, permitted-value, and size validation.
- Versioned startup policies and an explicit decision order: block, flag, allow.
- Observe mode separates recommended action from effective action.
- Errors, timeouts, skipped checks, and unsupported required checks produce an
  incomplete assessment, never an implicit successful check.
- No raw prompt or response retention by default.

## Adapter interface

An async checker accepts a request, trusted policy, and stage. It returns checker
identity and version, status, findings, and a simulation label. The engine supplies
the deadline and records latency. Provider errors must not expose exception text,
source content, or credentials. Scores from unrelated checkers must not be averaged
into one confidence number. External data routing must be approved before adding
live adapters. Local-only prototype cost fields must change when live providers
are introduced.

## Planned component evaluation

Use Pydantic and JSON Schema for deterministic checks. Evaluate Presidio for local
personal information handling, Bedrock ApplyGuardrail as a first managed safety
adapter, OpenAI Moderation as an optional alternative, and Promptfoo for regression
evaluation. NeMo, selected Guardrails AI Hub validators, Azure Prompt Shields,
Model Armor, Instructor, Outlines, and Garak remain candidates where requirements
justify them. LLM Guard was archived when checked; do not make it the main
dependency without a maintenance plan. Check licenses of packages and model
weights separately before adoption.

The optional local PII adapter reuses Presidio's email, credit-card, and U.S. SSN patterns
and validators without invoking its NLP engine. It scans decoded JSON as well as
text, uses an offline domain snapshot, and returns generic entity findings without
source values. The current scope excludes names, addresses, phone numbers, and
full PHI classification. Missing required adapters remain incomplete assessments.
Version 0.5 adds policy-controlled redaction, a separate transformation result,
and full active-check revalidation before returning transformed content. Observe
mode returns a preview only; enforcement can recommend using rechecked content.
Schema failures, sensitive JSON keys, and uncompleted or simulated rechecks
withhold transformed content. Redaction does not establish full anonymization.

Version 0.6 adds a local secret checker using selected detect-secrets provider and
private-key patterns under bounded regex execution. It inspects canonical text
and decoded JSON without credential verification, baseline files, or caller
suppression directives. Coverage is limited to supported patterns, not arbitrary
passwords, proprietary documents, or every current provider key format. The
example policy flags inputs and recommends blocking outputs in observe mode.

## Bias evaluation

Define reviewed rubrics for stereotypes, unsupported demographic assumptions,
and unequal treatment of equivalent support requests. Single-response concerns
need contextual evidence and uncertainty. Repeated matched scenarios are needed
to investigate group-level patterns. A mention of a demographic attribute is not
automatically a problem. Legitimate accessibility differences, quotations, and
criticism of discrimination must appear in benign test examples.

Do not infer fairness from a moderation result. Structured Outputs constrains
response structure, not truth, safety, or fairness. Neither validation nor an AI
checker replaces authorization at tool and data boundaries.

## Dynamic policies

Select checks using trusted application configuration and approved request
context. Never allow user text to change policy or silently relax required checks.
Future policy changes are drafts until an administrator publishes a version.
The prototype selects policy by application and version, and schema by stage.
Versioned JSON policy files load at startup through VALINATOR_POLICY_FILE.
Request-type routing and live policy management remain later milestones.
All adapters receive one canonical content field; JSON schema checks parse that
same content when content_format is json. A separate payload field is rejected.

## Acceptance and delivery

Version 0.4 adds pre-parser envelope size and nesting controls, supported format
assertions, declarative cross-field comparisons, stage-specific checker actions,
and a ten-group coverage catalogue shared by input/output APIs and the dashboard.
Unsupported, disabled, partial, and simulated coverage must remain distinct from
passed checks. The example business policy compares submitted dates and amounts;
independent verification of business facts requires trusted context integration.

The foundation changes were informed by OWASP's Input Validation Cheat Sheet,
Nightfall's model input validation overview, Splunk's validation lifecycle article,
and review of the tools in Galileo's comparison. Use vendor primary documentation
for integrations: the comparison's Bedrock-only limitation is incorrect and its
linked Galileo Protect solution is deprecated in favor of Agent Control.

Additional references:
- https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- https://www.nightfall.ai/ai-security-101/model-input-validation
- https://www.splunk.com/en_us/blog/learn/ai-model-validation-ml.html
- https://galileo.ai/blog/best-llm-input-output-validation-tools
- https://python-jsonschema.readthedocs.io/en/stable/validate/

Test decision semantics, application isolation, timeouts, error privacy, and
schema boundaries before a live adapter. Before a pilot, add reviewed cases with
real violations and benign lookalikes; report precision, recall, false alarms,
sample counts, latency, and cost by checker. Pilot thresholds and hosting are
open decisions. No universal bias-free or prompt-injection-proof claims.

## References

- https://developers.openai.com/api/docs/guides/moderation
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://aws.amazon.com/bedrock/guardrails/
- https://docs.nvidia.com/nemo/guardrails/
- https://microsoft.github.io/presidio/
- https://github.com/promptfoo/promptfoo
- https://doi.org/10.6028/NIST.AI.600-1

## Labeled text completeness (implemented)

`completeness` belongs to the Business rules group alongside `cross_field`.
Policies configure up to 50 `required_sections` with unique rule identifiers and
case-insensitive unique ASCII labels, stage scope, and a minimum non-whitespace
character count. Exact line-start `Label:` syntax is recognized, including
multiline bodies; any other valid label ends the preceding body. Missing, short,
or repeated required sections produce findings. Unsupported content format or
no stage requirements produces skipped, and required skipped checks produce an
incomplete evaluation. Existing stage actions, observe/enforce behavior, failure
handling, and redaction rechecks apply unchanged.

This is structural presence, not semantic/document adequacy. It does not interpret
Markdown, files, claims, consent, or medical/legal sufficiency. Labels within
quoted text are still literal labels. API findings contain rule identifiers and
generic explanations only. The local dashboard includes failing and editable
passing examples through the production check endpoints.

## Bounded text obfuscation signals (implemented)

The opt-in `text_security` adapter is the first limited checker in the Prompt
injection and manipulation group. It identifies selected hidden/control code
points in plain text and parsed JSON keys/string values. It excludes ZWJ/ZWNJ to
preserve common script/emoji usage. It also parses explicit case-insensitive
`base64:`/`hex:` whitespace-delimited tokens; standard Base64 or byte-pair hex
must decode as UTF-8. It flags encoded content even when benign and separately
notes selected controls inside decoded text. It never interprets, returns,
sanitizes, or recursively decodes that text, nor sends it to other adapters.

Per-request budgets: 16 tokens, 4,096 characters per token, 8,192 total encoded
token characters. Limits apply across all JSON units. Budget exhaustion or invalid
canonical JSON returns error, not passed. The required-check failure contract and
stage-specific policy actions apply normally. Findings are generic categories
without submitted or decoded values; `request_context` is outside coverage.

No semantic injection/jailbreak, encoded PII/secrets, arbitrary encodings,
homoglyph, Unicode-tag, image, or multi-turn detection is claimed. Signals can be
legitimate. The group limitations and dashboard examples expose this explicitly.
The implementation uses only existing Python standard-library functionality.
