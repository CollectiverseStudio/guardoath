# Prompt-attack detector integration decision

Reviewed October 28, 2026. Scope: a maintainable, optional input-only detector
connector with honest routing/cost disclosure and no paid development calls.

| Candidate | Evidence | Decision for this increment |
| --- | --- | --- |
| Protect AI LLM Guard | Official repository archived July 9, 2026; README says associated models/code are no longer maintained | Do not introduce the archived toolkit as a new dependency |
| Protect AI DeBERTa injection v2 | Apache-2.0 model card; archive notice; English-only prompt-injection scope, not jailbreak detection; ONNX file about 739 MB | Keep out of the default lightweight dashboard profile; no model was downloaded |
| NVIDIA NeMo Guardrails | Maintained framework with several local/model/service-backed guardrail options | Retain as a future deployment option; framework adoption needs separate model/runtime evaluation |
| Azure Prompt Shields | Current official REST contract with user-prompt/document attack booleans | Implement input-only connector, disabled until explicit configuration; validate protocol offline |

Sources:

- https://github.com/protectai/llm-guard
- https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2
- https://huggingface.co/protectai/deberta-v3-base-prompt-injection-v2/tree/main/onnx
- https://docs.nvidia.com/nemo/guardrails/latest/home
- https://learn.microsoft.com/en-us/rest/api/contentsafety/text-operations/shield-prompt?view=rest-contentsafety-2024-09-01

Implementation is not provider activation or calibration. Before live use, choose
an Azure resource/region, review provider terms and allowed data classes, define a
test budget, and evaluate benign lookalikes and attack fixtures. Default local
startup remains offline with respect to check-content egress. The connector sends
original text only after explicit policy permission and all configured real local
checks pass. The sample gates on PII, secret and text-security checks. This is
limited detector coverage, not proof of complete anonymization or safety.

No superiority/accuracy claims, guaranteed residency/retention, or automatic
fallback routing are inferred from vendor materials. No resource was provisioned,
credential requested, paid call made, or model downloaded in this increment.
