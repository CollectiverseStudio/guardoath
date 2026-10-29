# Hosting and validation providers are independent

GuardOath is a portable FastAPI/ASGI application with separate input/output HTTP
endpoints. Its engine, policies, findings, local detectors and regression runner
run without an AWS account, cloud SDK or managed validation service.

| Layer | Current decision | Portability boundary |
| --- | --- | --- |
| Product API | POST /v1/checks/input and /v1/checks/output; JSON findings/decisions | No cloud-specific request contract |
| Core validation | Local libraries and explicit versioned policies | Runs locally or on another ASGI-compatible host |
| Optional content assessment | `content_safety` adapter interface; provider defaults to none | Selected explicitly; never inferred from AWS hosting |
| AI generation | Called separately by the integrating application; dashboard currently accepts a pasted proposed answer | Tokatlas or another model provider can be used independently |
| Planned demo hosting | CloudFront interface and Lambda API; Greg handles implementation separately | No deployment resources or Lambda code included in this batch |
| Other deployment | Run the same ASGI app with uvicorn or another compatible host | AWS SDK extras are optional and only needed for the selected validation adapter |

The initial content-safety adapter uses Bedrock. That is an available integration,
not a claim of interchangeable vendor accuracy or equivalent category coverage.
A replacement must normalize the CheckerResult contract, declare itself external,
handle deadlines/failures, preserve safe explanations and document its own
coverage. An adapter can be registered with Engine under `content_safety` without
changing the application-facing API or policy rule. Application metadata exposes
the selected provider when registered; check versions/rule IDs retain provenance.
No automatic fallback is implemented: a provider outage must remain visible.

An explicit startup `GUARDOATH_CONTENT_SAFETY_PROVIDER=none` keeps the portable
core local. The optional `bedrock` value requires its own region/resource/version
configuration; the normal launcher overrides inherited selection back to none.
Existing Azure Prompt Shields remains a separate optional input-only connector.
There is no implicit call to either provider and no required Azure dependency.

Provider calls are distinct from the customer's model call. The local-first
external gate protects registered validation calls only; Joe's independent AI
call must obey GuardOath's result. Neither a schema pass nor a formatting check
establishes semantic safety, privacy compliance or absence of bias.

See [content-safety coverage](content-safety-coverage.md), the
[user guide](user-guide.md).
