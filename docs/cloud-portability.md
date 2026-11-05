# Hosting and validation providers are independent

GuardOath is a portable FastAPI/ASGI application with separate input/output HTTP
endpoints. Its engine, policies, findings, local detectors and regression runner
run without an AWS account, cloud SDK or managed validation service.

| Layer | Current decision | Portability boundary |
| --- | --- | --- |
| Product API | POST /v1/checks/input and /v1/checks/output; JSON findings/decisions | No cloud-specific request contract |
| Core validation | Local libraries and explicit versioned policies | Runs locally or on another ASGI-compatible host |
| Content assessment | Local `harmful_content`, `human_safety` and `prompt_attack` checks (2026-11-05) | No outside provider; nothing leaves GuardOath |
| AI generation | Called separately by the integrating application; dashboard currently accepts a pasted proposed answer | Tokatlas or another model provider can be used independently |
| Planned demo hosting | CloudFront interface and Lambda API; Greg handles implementation separately | No deployment resources or Lambda code included in this batch |
| Other deployment | Run the same ASGI app with uvicorn or another compatible host | The AWS SDK extra is needed only for the DynamoDB tenant store |

Every check runs inside GuardOath (2026-11-05, Greg): no check sends content to another company's service. The two
former optional connectors, `content_safety` (Amazon Bedrock Guardrails) and `azure_prompt_shields` (Azure Prompt
Shields), were removed with their code and settings; the local `harmful_content` and `prompt_attack` checks do that
work. The engine no longer has an external-provider route, so moving GuardOath to another host changes nothing about
where content goes.

The customer's own model call is separate: Joe's application calls its AI model
itself and must obey GuardOath's result. Neither a schema pass nor a formatting check
establishes semantic safety, privacy compliance or absence of bias.

See the [user guide](user-guide.md) and [tenant records](tenant-records.md).
