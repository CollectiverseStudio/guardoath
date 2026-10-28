# Secrets and confidential information: supported batch

Acceptance scope: implementation and authored regression expectations below are
delivered as one group batch, with main CI verification required before delivery. This is not a claim
that every secret, password, provider format or proprietary document is detected.

| Area | Supported acceptance scope | Evidence |
| --- | --- | --- |
| Credential patterns | AWS, GitHub classic/fine-grained candidates, GitLab, supported OpenAI, Stripe, Slack, SendGrid, Twilio identifiers, npm registry assignments, PyPI/TestPyPI, Azure Storage, URL credentials, supported Discord/Telegram, JWT and private-key markers | Provider input/output fixtures; stage/mode and variant tests |
| Literal credentials | Opt-in quoted secret-named assignments and decoded JSON string-field context; references/placeholders and benign names tested | Assignment/JSON/benign matrix |
| Confidential phrases | Up to 50 neutral-ID rules; 3–200 char literal terms; Unicode NFKC/casefold; stage scope and duplicate checks | Positive/negative/normalization/stage/config tests |
| Encodings | Opt-in explicit Base64/hex, one UTF-8 layer; shared 16/4096/8192 count/token/total budgets | Decoded positives, benign payloads, no-recursion, invalid/over-budget tests |
| Decisions | Observe/enforce, input flag/output block recommendations, required failure semantics | Mode/stage matrix and enforce fixtures |
| JSON and privacy | Bounded parsing, keys/string values/nesting/escapes, no values in metadata/findings/logs/reports, no secret redaction | Parser/API/report assertions |
| Egress | No verification/network; secret findings hold registered external checks | Socket/request guards and provider-by-provider gate tests |
| User experience | Three new dashboard examples, corrections release preview, whole-group launcher/report, same-suite baselines | Browser and launcher tests |
| Documentation | Purpose/use case/result/limits for each supported check and every authored group case | `docs/user-guide.md` and catalog completeness test |

Run `python -m valinator.evaluate --cases evals/secrets.json --policies
policies/secrets-batch.json` for 100 authored expectations. Eight intentionally
expect incomplete evaluations. `tests/test_secrets_check.py` and
`tests/test_secrets_batch.py` expand those examples into the provider/stage/mode/
JSON/privacy/failure matrix; separate API/engine tests cover infrastructure.
These are regression assertions, not independent precision/recall or calibration.

Remaining boundaries: arbitrary passwords/prose/bearer headers, all language
syntax, numeric JSON credentials, recursive/unlabeled encodings, arbitrary Unicode
smuggling, file/OCR/image scanning, secret redaction, credential activity/signature
validation, semantic IP/trade-secret classification, and all future provider
formats. `request_context` is not scanned. A configured literal term can flag
negations/educational mentions and cannot recognize unconfigured confidential
ideas. Recognized placeholders can hide real reference-looking credentials.

The user guide and backlog must change with the next group batch. Existing
policies retain opt-in defaults; the extended profile is explicit, and the Azure
sample enables assignment/encoding checks in its local-first gate. The normal
launcher remains local-only.
