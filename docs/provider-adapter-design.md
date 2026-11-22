# Provider adapter design

**Status: proposed design, reviewed against provider documentation on 2026-11-22. No hosted provider invocation, connector registration, interaction ledger, or new endpoint is implemented by this document.**

## Recommendation and current boundary

Use a small provider-neutral GuardOath contract with optional provider adapters. Boto3 is the appropriate AWS transport for IAM-authorized Bedrock model calls; it is not the common transport for every AI product. Start with a bounded, nonstreaming Bedrock `Converse` adapter behind the same contract that will support direct OpenAI, Microsoft, and Anthropic adapters. Customer choice remains explicit; adding AWS support must not make AWS the default provider for other tenants.

The hosted API currently exposes independent input/output checks. The separate [gateway prototype](../guardoath/gateway_prototype/README.md) demonstrates input checks, a provider call, and buffered output checks, but its `/v1/interactions` route is not mounted in the hosted app. Its process-local idempotency cache and call budget are not production controls. The [source-of-record contract](ai-agent-activity-system-of-record.md) remains authoritative for durable evidence and customer-selected models, agents, and executors. The [published check contract](api-contract.md) remains unchanged.

## Keep the integration surfaces distinct

These are candidate adapters, not a list of currently supported GuardOath integrations.

| Customer choice | Provider surface and binding | Boundary to preserve |
| --- | --- | --- |
| AWS-hosted model | Boto3 `bedrock-runtime.converse`; optional later `converse_stream`. Bind AWS account/role, region, and exact model or inference-profile ID. | Converse supports compatible message models; streaming, tools, modalities, and inference parameters still depend on the selected model. [AWS Converse](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse.html), [ConverseStream](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-runtime/client/converse_stream.html). |
| OpenAI model | OpenAI Responses API with an approved project credential and model ID. | A model response API is distinct from a ChatGPT integration. [Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create). |
| Microsoft model or Foundry workload | Bind the specific Azure OpenAI resource or Foundry project endpoint, authentication, deployment/model, and supported API. | Azure OpenAI and Foundry project Responses endpoints have different capability sets; validate the selected surface. Prefer Entra identity where supported. [Azure OpenAI Responses](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses), [Foundry project Responses](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/responses-api). |
| Direct Anthropic model | Claude Messages API with an approved credential, API version, and model ID. | Messages, tool execution, and an agent runtime are different capabilities. [Messages reference](https://platform.claude.com/docs/en/api/messages), [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview). |
| AWS AgentCore runtime | Bind runtime ARN, region, qualifier, payload schema, and authentication mode. IAM/SigV4 uses Boto3 `bedrock-agentcore.invoke_agent_runtime`; OAuth uses the documented HTTPS bearer path. | AWS explicitly excludes OAuth invocation from the AWS SDK path. Runtime payload/response schemas are agent-specific; invoking a runtime does not expose every internal tool call for preapproval. [InvokeAgentRuntime](https://docs.aws.amazon.com/bedrock-agentcore/latest/APIReference/API_InvokeAgentRuntime.html). |
| Existing Bedrock Agents Classic deployment | Separate legacy adapter using `invoke_agent`, agent ID, alias ID, and tenant-owned session mapping. | Classic closed to new customers on July 30, 2026. Do not select it for new onboarding. `RETURN_CONTROL` can expose configured action-group proposals; other internal actions are not thereby intercepted. [Maintenance notice](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-classic-maintenance-mode.html), [InvokeAgent](https://docs.aws.amazon.com/botocore/latest/reference/services/bedrock-agent-runtime/client/invoke_agent.html), [return control](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-returncontrol.html). |

ChatGPT, Copilot Studio, and Claude applications need their own supported integration channels: for example, a ChatGPT plugin/MCP service, a published Copilot Studio agent connection, or a Claude connector/plugin. Those channels can call GuardOath, but installing one does not guarantee interception of every conversation or internal model/tool operation. Microsoft documents Direct Line and a separate Agents SDK integration for published Copilot Studio agents. [ChatGPT plugins](https://developers.openai.com/plugins), [Copilot Studio custom-app integration](https://learn.microsoft.com/en-us/microsoft-copilot-studio/publication-connect-bot-to-custom-application), [Claude connectors/plugins](https://claude.com/marketplace/connectors-plugins).

## Proposed request and connector fields

The following illustrates a future model request. **It is not accepted by the current check endpoints or the existing prototype schema.** Final endpoint/schema publication requires implementation and acceptance tests.

```json
{
  "application_id": "customer-application",
  "request_id": "customer-idempotency-key",
  "target": {
    "kind": "model",
    "connector_id": "tenant-approved-connector",
    "model_id": "tenant-approved-model"
  },
  "input": {"text": "Explain the refund policy."},
  "generation": {"max_output_tokens": 512},
  "delivery": {"mode": "buffered"}
}
```

Require `application_id`, `request_id`, `target`, and input. Derive tenant, principal, entitlement, and saved policy from authenticated server state. Make generation limits optional within server-enforced ceilings; start with text and the language coverage the validators actually support. No caller-supplied model instruction, attachment, conversation history, or unchecked context may reach a provider through a bypass around input validation. Unsupported modalities and parameters must be rejected before dispatch, rather than dropped or silently translated.

A model target requires a permitted connector and model/deployment identifier. A managed-agent target instead requires a permitted agent binding; a caller cannot override its underlying model unless that integration explicitly supports and authorizes the override. Agent/executor IDs describe actual selected integrations, not decorative metadata.

Register connectors through a separate trusted tenant configuration flow. Persist the connector's tenant/application ownership, version, adapter kind, allowed target IDs, region/endpoint, auth mode and credential reference, capability/schema version, timeout ceilings, quota/cost limits, and data-handling policy. Keep secrets out of invocation JSON. Bind external conversation, response, and runtime-session IDs to the same tenant, principal, connector, and target before reuse; generate server-owned mappings rather than accepting arbitrary provider session IDs.

For AgentCore, registration must additionally bind `agentRuntimeArn`, an allowed `qualifier`, `contentType`/`accept`, and a versioned payload/response schema. Resolve `runtimeSessionId` from the trusted mapping. Do not forward caller-controlled runtime user IDs, auth headers, endpoint URLs, or tracing baggage. AgentCore's binary payload is not a universal chat-message schema.

## Fast implementation without weakening enforcement

Keep adapter responsibilities narrow: validate capabilities, serialize approved input, invoke one configured provider, parse complete typed output/tool proposals, and report provider request IDs, usage provenance, stop reason, and outcome. The gateway owns authentication, tenant policy, durable idempotency, cost reservation, checks, and release decisions.

For the first AWS path, map approved text into `Converse.messages`, the configured target into `modelId`, and supported generation limits into `inferenceConfig`. Initially omit tools, provider-managed retrieval, and provider-managed conversation state. This provides one real guarded model call without prematurely claiming control over remote agent side effects. Add a second provider to the same acceptance suite before declaring provider neutrality proven.

Reuse initialized Boto3 clients and HTTP pools per process, keyed by tenant/connector version, region, and credential identity. Bound the client cache, expire obsolete connector versions, and close unused pools. Create sessions/clients outside concurrent request handling; Boto3 clients are generally thread-safe with documented caveats, while sessions/resources are not interchangeable concurrency primitives and clients must not be shared across processes. Run synchronous SDK I/O through a bounded worker pool in an async service; cancellation of an await does not prove the remote request stopped. [Boto3 client guidance](https://docs.aws.amazon.com/boto3/latest/guide/clients.html).

Set explicit connection/read timeouts and an overall request deadline that leaves time for output validation and durable recording. Keep connection-pool size, worker count, tenant concurrency, and admission limits consistent. For initial billable/side-effecting calls, use one SDK attempt (`retries={"mode": "standard", "total_max_attempts": 1}`); retry only when an adapter can establish a safe condition and the durable attempt policy permits it. A timeout after dispatch means outcome/usage may be unknown, not that nothing ran. Botocore's socket timeouts are not a complete wall-clock deadline. [Botocore configuration](https://docs.aws.amazon.com/botocore/latest/reference/config.html).

Use AWS workload identities or tenant-delegated, least-privilege roles with temporary credentials. Cross-account roles need a tenant-specific external-ID binding and constrained trust/resource policies; the external ID is not a secret or a substitute for authorization. Boto3's configured assume-role provider can refresh temporary credentials; manually supplying an expiring STS tuple does not establish that refresh behavior. Do not mutate process-global AWS credentials when switching tenants. [Third-party role access](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_common-scenarios_third-party.html), [Boto3 credentials](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).

Client reuse removes avoidable setup work; it cannot guarantee model latency. Measure cold/warm p50/p95/p99 separately for admission, input checks, provider time, output checks, and durable writes. Preserve the existing PII-check deadline; it is not an end-to-end model-response promise. Do not raise production Lambda timeouts, concurrency, or spend limits implicitly. Long agent tasks need a separately reviewed asynchronous job/receipt contract.

## Streaming, actions, and the source of record

Default to buffering the complete provider answer until output checks and any redaction recheck succeed. `ConverseStream` can be consumed internally for progress while keeping content private. Forwarding tokens before whole-answer checks changes the guarantee: already released content cannot be recalled. Any future chunk-release mode needs explicit policy compatibility, boundary-aware checks, bounded buffering/backpressure, and separate acceptance evidence. Never execute partial streamed tool arguments.

For tools, require a controlled proposal/approval/execution boundary with exact arguments, resource, tenant, principal, policy, expiry, and executor binding. Remote hosted agents or server-side tools may execute before returning a final answer. An outer input/output wrapper cannot claim to have prevented those side effects; unsupported enforcement requirements must reject that configuration. Return-control or a cooperating customer executor must cover each relevant action, not only one selected tool.

Implement the existing source-of-record contract before presenting a durable activity timeline: persist admission and dispatch intent, record each attempt and terminal/unknown outcome, and reserve quotas atomically across instances. Record connector/version, requested and provider-observed model/agent identity separately, provider request IDs, policy/check evidence, approval/execution evidence, timing, and usage provenance. A configured model label is not proof of the underlying model selected by a managed agent. Preserve `gateway-observed`, trusted host-reported, and unverified evidence levels. Do not persist raw prompts, rejected output, credentials, or unsanitized provider errors by default.

Fail closed before dispatch if durable admission cannot be recorded. After an ambiguous network or persistence failure, reconcile instead of blindly repeating an invocation. The prototype's local lock/cache cannot supply this guarantee across Lambda instances.

## Smallest reviewable delivery

1. Implement the shared admission/evidence boundary and one optional, buffered text Bedrock adapter with mocked/stubbed tests; keep provider calls disabled until credentials, quotas, privacy, and cost authorization are concrete.
2. Verify zero dispatch on blocked input, cross-tenant connector/session refusal, unsupported-capability refusal, redaction rechecks, duplicate concurrent admission, unknown outcomes, and secret-free errors/events. Test a second provider with the same contract.
3. Run separately authorized bounded real-provider acceptance and latency measurements. Only then publish supported connector capabilities and make the dashboard invoke that exact API.
4. Add AgentCore, application channels, tools, and streaming as explicit capability increments. Each must demonstrate its actual interception and evidence boundary; none inherits a guarantee merely from using an official SDK.
