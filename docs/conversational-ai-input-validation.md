# Conversational AI Input Validation — unified GuardOath implementation contract

**Status:** Architecture and measurable acceptance specification; NOT a claim of semantic coverage or a new production feature. **Scope:** user chat questions, multi-turn conversations, retrieved context, and proposed AI answers/actions. Structured ML schema checks use existing schema and data-quality controls rather than creating a second engine.

## Why this is needed

The existing GuardOath tenant record configures 19 checks and 524 static rules, including local `prompt_attack`, `human_safety`, `harmful_content`, `lexical_guards`, `pii`, `secrets`, `tool_calls` and schema-related controls. Those checks are valuable, but local prompt-attack and human-safety rules are primarily phrase/regex indicators. A configured checker firing is not proof of context-sensitive semantic understanding. No new hardcoded category count is proposed; these are **coverage capabilities** that map to existing checks or future tenant-configured rules.

## Security boundaries and processing order

1. **Authenticate and authorize the envelope.** Tenant identity comes from verified credentials, not a client-provided `tenant_id`; bound request size, per-tenant metering and stage applicability before allocating expensive work. Only privileged server-side configuration can change trust roles. The caller's `role`, quoted `system` text, or content resembling JSON messages cannot confer privilege.
2. **Build inspection views without rewriting meaning.** Preserve original bytes/text in controlled request memory; derive bounded UTF-8/Unicode-normalized and canonical inspection views. Treat decoded or extracted material as **untrusted**; normalization does not magically make it authoritative. Avoid unsafe repeated decoding or confusable-character stripping that changes legitimate content. Ensure invalid encoding / parser timeout is incomplete or rejected according to tenant policy.
3. **Select enabled checks from the authoritative tenant record.** Preserve per-stage control and rule actions, explicit Off, observe/enforce, failure actions, and tenant isolation; retrieve all findings rather than first-match short circuit. Separate applicability from clean results. Do not run file-format checks without actual authenticated attachment bytes. See Issue #32.
4. **Validate the chat user instruction.** Check policy/role spoofing, jailbreaks, prompt extraction, secrets/PII, harmful requests and genuine crisis signals; distinguish educational quotation, translation, fiction, and clinical contexts when evidence supports it. For uncertain semantics, report uncertainty/Flag or Block as tenant policy requires; do not claim lexical matching understands user intent.
5. **Validate lower-trust data independently.** Retrieved pages, files, search snippets and tool outputs are *data* even when they contain imperatives. Reject attempts to promote their instructions into system/developer authority; enforce tool allowlists and object-level authorization separately. The user's requested task is not the same as an untrusted document's instructions.
6. **Enforce one effective decision.** Existing strongest-action, stage, timeout and fail-closed behavior applies. Redaction must be supported by an explicit configured transformation: never silently replace arbitrary messages or leak raw findings. An enforce Block stops model invocation. A Flag may proceed with audit metadata; Allow is not a claim of safety.
7. **Validate model output and requested actions.** Run output checks and independent tool authorization before releasing content or executing state-changing calls. Prevent cross-tenant disclosure and require explicit authorization for high-risk actions. Preserve actual model output and security provenance separately.
8. **Report privacy-safe receipts.** Return stable check/rule IDs, stages, configured/recommended/effective actions, evaluation status and generic reason codes. No raw secrets, recovered prompt, private documents or sensitive text in logs/telemetry/evidence.

## Coverage matrix: capabilities, owners, and measurable gaps

| Capability | Current existing checks/layer | Acceptance gap / next action |
| --- | --- | --- |
| Request integrity: size, encoding, malformed content and budget | API / models / engine (shared Director-owned) | Explicit stage-scoped failures, no model invocation on enforce Block |
| Prompt override, fake roles and leaked instructions | `prompt_attack`, `lexical_guards` | Paraphrases, quoted training examples, multilingual variants and false positives need **measured** calibration; never assert semantic parity |
| Secrets / PII and redact semantics | `secrets`, `pii`, `encoded_pii` | Respect record actions; isolate 500-ms intermittent failures (Issues #27/#29/#30) |
| Human crisis / harmful intent | `human_safety`, `harmful_content` | Distinguish first-person imminent crisis from creative/clinical look-alikes, without suppressing support routing |
| Domain and task scope | Tenant application policy / future scope rules | Explicit allowed intents, refusal/Flag behavior, multilingual and borderline examples; no silent global-topic policy |
| Multi-turn conditioning / context drift | Existing lexical indicators only partially | Authenticated bounded conversation envelope with role provenance; sequential attack fixtures and single-turn counterexamples |
| Data provenance / RAG injection | `prompt_attack`, `lexical_guards`, retrieval boundary | Server-tagged trust sources, instruction/data separation, tool response poisoning fixtures |
| Agent tool and object authorization | `tool_calls`, existing tenant scopes and API | Hard authorization outside text screening; malicious arguments, harmless queries, dry-run and two-finding tests |
| File & Document Security | `csv_formula`, byte-only `attachment_probe` | Issue #32: actual file bytes, `auto/off` semantics, unsupported fail-closed, not a fabricated PDF/Office scanner |
| Cross-tenant integrity and reporting | Tenant record / engine | Verify two tenant policies receiving same string independently; no raw finding text or other tenant data |

## Twelve chat-specific canonical acceptance scenarios

Saved evaluation cases must include each scenario's input and output stage, policy version, tenant settings, expected rule IDs *where deterministically supported*, evaluation status, recommended/effective action, and whether the AI gateway was called. **Do not mislabel proposals as tests that already pass.**

| ID | Input category | Positive / benign pairing | Required property |
| --- | --- | --- | --- |
| CHAT-001 | Ordinary FAQ | Capital city question / policy-neutral follow-up | No invented security finding |
| CHAT-002 | Explicit override | Directly asks to ignore higher-priority rules / tutorial *describing* that phrase | Attack detection without blanket blocking of education |
| CHAT-003 | Role forgery | Text claiming to be `system` and ordering disclosure / user asking what a system message is | Client text never changes trusted role |
| CHAT-004 | Prompt exfiltration | Solicits confidential developer instructions / asks for a public help article | No protected instructions leave the system |
| CHAT-005 | Sensitive personal data | Synthetic patient identifiers / nonsensitive numeric examples | Configured Block/Flag/Redact and zero value echo |
| CHAT-006 | Credentials | Synthetic API credential / benign string mentioning keys | Redacted or blocked according to tenant |
| CHAT-007 | Human crisis | Credible immediate self-harm statement / third-person fictional description | Supportive crisis route, no indiscriminate silent drop |
| CHAT-008 | Conversation conditioning | Multiple turns gradually requesting forbidden disclosure / legitimate follow-up | State-aware authorization and context integrity; no one-turn-only overclaim |
| CHAT-009 | Multilingual meaning | Equivalent synthetic attack paraphrased in a second language / translation request | Report measured coverage instead of pretending regex equals semantics |
| CHAT-010 | Retrieved page injection | Webpage containing role instructions / a page quoting a security warning | Untrusted page content cannot become developer instruction |
| CHAT-011 | Tool abuse | Request to delete unauthorized records / authorized read-only lookup | Tool permission independently enforced |
| CHAT-012 | Real attachment | CSV formula found in received bytes / chat merely saying “attached CSV” | Actual bytes activate matching rule; no file mention activation; unsupported PDFs cannot be passed as scanned |

For each scenario, include a positive and benign look-alike where meaningful, plus observe/enforce, stage-specific Off, Block/Flag/Allow/Redact where supported, two simultaneous independent findings, malformed/time-budget fail-closed, and privacy-safe receipts. Use only synthetic content. The total number of test *executions* will exceed 12 due to parameterization.

## Proposed API integration, not a separate endpoint implementation

Use the **existing authenticated** input/output check API and existing tenant controls. A future conversation wrapper should bind a server-authorized `conversation_id` and trusted turn roles to message content, returning the current engine's result contract. Content and `role` fields supplied by untrusted clients **must not** be considered new privileges. The wrapper may call the AI only after an enforce decision permits it, and must route output and tool actions through the same policy engine. Coordinate any `guardoath/app.py`, `models.py`, `tenant_record.py`, `engine.py`, dashboard or CI edits with the Director in `docs/director-agent-coordination.md` before starting.

## Prioritization and proof requirements

- **P0 Atlas-owned evidence:** Document and generate synthetic positive/benign/paraphrase/multilingual/multi-turn scenarios; classify each as covered, partial, or not implemented with exact source and tested result. No assertion that a regex understands semantics.
- **P0 Director/shared architecture:** Agree on role provenance, tenant-bound conversation history, model-call gate, tool authorization and multi-turn context. Shared-file changes require explicit acknowledgment.
- **P1 integration:** No siloed chat validation engine, no duplicate rule catalog, no independent tenant store, no new paid outside classifier without Greg-approved cost/security review.
- **P1 release proof:** Focused acceptance and full 13 Tests/3 CodeQL jobs at exact release SHA. Post-deploy 15 live checks are verification, not a removed production approval gate.

## External references

- https://www.vibeprod.ai/blog/input-validation-in-ai-code-a-developers-guide
- https://www.nightfall.ai/ai-security-101/model-input-validation
- https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html

These are design references, not evidence that any cited defense is implemented.
