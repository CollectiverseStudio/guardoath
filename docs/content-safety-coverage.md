# Content safety: provider-independent contract and initial adapter

Version 0.21.0. Hosting and validation-provider selection are independent.
The stable checker ID is `content_safety`; the initial optional adapter uses
Amazon Bedrock ApplyGuardrail. No cloud SDK is required by the portable core,
no provider is chosen from hosting metadata, and neither deployment nor a normal
local launch enables provider calls. Another trusted adapter can implement the
same asynchronous Checker contract and be registered with Engine without changing
API requests, policies or the dashboard. This is tested with an independent
in-memory adapter; it is not a claim that additional live providers are integrated.

## What is checked

The initial adapter requires a dedicated, published numeric guardrail version
with all five text content filters active on both stages: HATE, INSULTS, SEXUAL,
VIOLENCE and MISCONDUCT. Strengths and block/detect behavior live in that reviewed
provider configuration, not in fabricated portable numeric scores. The adapter
requests FULL assessments and requires all five categories, an active strength,
a valid confidence and an explicit Boolean detected value for each category.
Missing/duplicate/inactive categories, extra assessment policies and unexplained
interventions are incomplete results, never a pass. This conservative contract
must still be confirmed against a real configured guardrail before live reliance.

`detected: true` produces a finding even if AWS detect mode returns action NONE.
AWS confidence is displayed as provider confidence, not a probability or local
harm severity. All such findings use GuardOath medium review severity. Stable
rule IDs are `harmful_content.bedrock_hate`, `_insults`, `_sexual`, `_violence`
and `_misconduct`. Findings include fixed category/confidence/strength labels;
provider output text, matched content, actionReason, credentials and resource
identifiers are excluded. Ordering follows the fixed category list.

The sample policy is observe mode: input findings recommend flag; output findings
recommend block; effective action remains allow in observe mode. The dashboard
holds the ordinary answer whenever any finding or incomplete check exists.
Enforce mode applies the recommendation, and required-check errors use the
configured failure action. Joe's application must implement the decision.

## Routing and operational limits

- Provider selection defaults to `none`. Explicit `bedrock` selection is required.
- Policy permission must be `after_local_pass`; every selected local check must
  pass without simulation. The sample selects PII, secrets and text-security.
- The adapter sends only the submitted stage's content, never request_context.
  It sets INPUT or OUTPUT independently. The output endpoint does not itself
  prove the caller previously submitted/passed an input; Joe coordinates that flow.
- Plain text only, at most 10,000 Unicode code points. This is a conservative
  GuardOath cap, not a statement of AWS service quotas. No splitting/truncation.
- Published numeric version, explicit commercial-AWS region and short resource
  ID only. No arbitrary URL, ARN override, DRAFT, retries or fallback provider.
- boto3 uses the standard credential chain, preferably a workload IAM role.
  No provider credentials enter the browser. Client/credential lookup is lazy.
- The regional HTTPS endpoint is explicit, TLS verified, configured endpoint
  overrides and HTTP proxies are disabled for the runtime call. Credentials may
  still use the SDK's normal trusted configuration/role services.
- Two worker slots per adapter. Engine deadlines do not kill an in-flight SDK
  thread; its slot remains occupied until the worker actually ends. Further
  calls return `bedrock_busy` rather than creating an unbounded queue. Lambda
  can freeze/thaw those workers; a live cold/warm-load test remains required.
- SDK connect timeout is 1 second; read timeout is capped at 5 seconds and the
  policy timeout. Credential resolution/DNS can outlive the API deadline.
- Response parsing is performed by the SDK before the bounded assessment-shape
  checks; there is no independent wire-byte cap in this adapter.
- Cost is unknown (`cost_usd: null`, `provider_unpriced`), including conservative
  metadata for unavailable selected external checks. No free-service claim.

No dedicated self-harm classifier, image/video moderation, general contextual
bias evaluation, fact checking, legal determination, emergency response or
certification is provided by this integration. A no-finding result only describes
these selected completed checks under the configured guardrail.

The adapter also requires explicit text coverage counts matching the entire
submitted string. Missing, partial, wrong-type or mismatched counts are
incomplete, not a pass. Character-count behavior for non-ASCII real traffic must
be confirmed during live calibration; conservative mismatches hold the request.

## Offline test inventory

Run with optional dependencies installed:

```bash
python -m pip install -e '.[test,pii,aws]'
python -m pytest tests/test_content_safety.py tests/test_bedrock_guardrails.py -q
```

| Test family | Coverage |
| --- | --- |
| 80 category contracts | Five categories x four confidence labels x detected true/false x input/output; detect mode is not silently allowed |
| 20 malformed contracts | Ten invalid shapes/types/configuration signals x both stages; enforce-mode errors hold content |
| Additional integration tests | Block mode, stable ordering, duplicate/missing categories, unsupported policy sections, error codes, missing IAM credentials, SDK deadlines, gate/privacy behavior, size/format limits, full Unicode boundary, retained worker slots, SDK request validation and lazy provider registration |
| Portable API tests | No SDK import when disabled, unsupported-provider rejection, replacement adapter with unchanged policy/API, unavailable-provider disclosure |
| Browser tests | Optional-provider disclosure, six independent examples per stage, category findings, output failures, clean fixtures, unchanged opposite editor/profile |

Tests use synthetic local SDK call fixtures and botocore Stubber. No resource is
created and no cloud/provider call is made. These are protocol and orchestration
checks, **not 100 model-accuracy examples**. The ordinary local evaluation runner
continues to refuse enabled external adapters. The new provider contract suite
is run through pytest, not through a misleading local moderation report.

## Primary references

Reviewed October 28, 2026:

- https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html
- https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ApplyGuardrail.html
- https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_GuardrailContentFilter.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-harmful-content-handling-options.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-content-filters.html

Before live activation, review the actual guardrail configuration, tier, region
and any cross-region profile, allowed data classes/provider terms, prices and
budget. The requested region alone does not establish processing residency.
Use independently reviewed benign/contextual and harmful examples, including
reported abuse, education and neutral identity language, before relying on
blocking. No live accuracy/residency/latency promise follows from these fixtures.
