# GuardOath unified validation architecture — six defense layers

**Status:** Architecture proposal and acceptance criteria, not a claim of implemented detection or deployment. This document consolidates Greg's six-domain blueprint with the existing [11-family input framework](ai-input-validation-framework.md), [human safety scenarios](human-safety-scenarios.md), [161 security indicators](security-indicator-use-cases.md) and [200-use-case catalog](use-case-manifest.json).

## Six structural domains

| Domain | Trust boundary | Required control | Primary failure mode |
|---|---|---|---|
| 1. Human safety and crisis intervention | User input and generated output | Stage-specific crisis, medical, child-protection, coercion and privacy review; supportive crisis intercept | Unsafe advice, missed emergency escalation or false-positive denial of help |
| 2. Prompt security and infrastructure firewalling | User prompt, retrieved text, tool results | Provenance isolation, injection analysis, bounded decoding, authorized tool dispatch | Untrusted text promoted into privileged instructions |
| 3. Privacy, IP and compliance | Ingress, retrieval and egress | Identifier and secret detection, tenant confidential terms, redaction with revalidation | Sensitive information disclosed or improperly retained |
| 4. Adversarial attacks and data integrity | Image/audio/file ingestion and training pipeline | Modality-aware size limits, integrity/provenance, statistical anomaly review | Poisoning, resource exhaustion, or adversarially altered input |
| 5. Schemas, tool gateways and agent actions | Trusted execution gateway | Strict schema, authenticated identity, tenant isolation, authorization, approval, budget, sandbox and egress | Unauthorized or destructive action executed |
| 6. Budgets and availability | HTTP ingress and model/provider calls | Raw byte/character/token budgets, bounded parsing, rate and spend limits, replay controls | Denial of wallet, service overload, runaway agent |

## Enforcement sequence

1. **Bounded ingress:** validate bytes, content type, size, encoding and parsing depth before expensive operations. Reject unsupported formats without claiming they were assessed.
2. **Identity and tenant context:** obtain authenticated principal, tenant, policy revision and provenance from trusted services, not model text.
3. **Stage-aware local checks:** run configured privacy, secret, lexical, schema and budget checks. Distinguish input, retrieved content, proposed tool calls and model output.
4. **Optional calibrated semantic and modality checks:** use model-based classifiers only when configured, available and evaluated for the applicable language/domain. Record incomplete results on provider failure; don't report a pass.
5. **Trusted tool dispatch:** validate schema, permissions, resource ownership, approvals, egress and execution budgets immediately before any side effect. No textual detector can substitute for this boundary.
6. **Output safety and transformation:** screen generated output; block dangerous instructions when policy requires, provide appropriate crisis support, and revalidate redacted content before release.
7. **Audit and feedback:** log rule IDs, policy revision, stage, action and safe metrics without raw sensitive prompts; use versioned evaluation sets and specialist review for human safety.

## Mathematical and statistical methods — validated uses and limitations

| Technique | Suitable use | Required calibration and caveat |
|---|---|---|
| Embedding cosine similarity | Candidate retrieval or triage of semantically related risky examples | A value such as **0.82 is only an illustrative candidate**, not a production cutoff. Calibrate by model, language, risk class, class prevalence, precision/recall and benign look-alikes. Similarity does not establish intent. |
| Latent-space/attention metrics | Research on models whose internal activations are actually available | Not generally accessible through hosted LLM APIs. Must not be a prerequisite for cloud-agnostic GuardOath validation. |
| Mahalanobis distance | Outlier detection in fixed-dimensional numeric features with suitable reference distributions | Requires stable covariance estimation, drift monitoring and adversarial evaluation. Does not by itself prove poisoning. |
| Character/token edit distance | Detection of known obfuscation patterns | Normalization may harm multilingual text; edit distance is not clinical intent classification. |
| Luhn Mod-10 | Checksum validation of supported payment-card candidates | Combine issuer/BIN-length checks, context, test data and privacy policy. Does not validate national IDs generally or prove a real account exists. |
| Compression ratio (including LZW) | A supplemental signal for highly repetitive input flooding | Not a synonym for entropy or tokenizer usage. A compressed document may be legitimate; benchmark cost and false positives. |
| Provider-aware token counting | Context-window and cost budgets | Use the relevant tokenizer or conservative model-specific estimation; reject before the paid model call when over budget. |

## Decision semantics and human safety

- **Allow:** checks passed with sufficient evidence and trusted authorization.
- **Flag:** concerning signal requires review or a nonblocking warning; not equivalent to an unsafe-content verdict.
- **Block:** enforce-mode policy denies an unsafe output or unauthorized action. The user should still receive safe crisis assistance when relevant.
- **Incomplete:** missing classifier, context, telemetry or provider response. Never silently convert incomplete to passed.
- **Redact:** transform supported sensitive identifiers and revalidate before forwarding.

Self-harm requests for help are not malicious by default. Emergency triage messaging and medication safety decisions require clinically reviewed responses. For imminent danger, direct to appropriate emergency services; U.S. suicidal crisis support can include 988. Avoid automatic punitive account actions solely from classifier output.

## Test and release requirements

- Each scenario has positive, benign look-alike, paraphrase, multilingual/Unicode, boundary, timeout and failure-mode fixtures where applicable.
- Verify input and output stages separately, plus cross-stage emergency omission detection.
- Measure confusion matrices, false-positive rates, missed high-severity cases and latency on versioned evaluation datasets. Never claim exhaustive human-harm detection.
- Confirm tenant-specific rule actions, observe/enforce semantics, provenance and safe logging.
- Require exact deployment SHA with all **12 CI jobs green**, a coordination-file deployment request, and **15 live checks** through the established deployment script. New AWS costs require a pricing review.
- Keep all source, tests, documentation and media in GitHub. Director owns shared engine, tenant, hosted API, dashboard and deployment files.

## Delivery order

1. Integrate and verify existing boundary and configuration indicators without duplicate findings.
2. Build stage-aware human-safety detectors from clinically and child-safety reviewed fixtures, with crisis response templates and uncertainty handling.
3. Add provider-aware token budgeting and bounded repetitive-input scoring.
4. Extend retrieval provenance and trusted tool-dispatch controls.
5. Build separate modality-specific ingestion validators and poisoning/anomaly evaluations.
6. Calibrate optional semantic embeddings with real false-positive/negative datasets before enabling automated blocking.

**No fixed numeric similarity, compression or anomaly threshold is approved for production by this document.**
