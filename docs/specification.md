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
The prototype selects policy by application and version, and schema by stage;
request-type routing and policy management are later milestones.

## Acceptance and delivery

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
