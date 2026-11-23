# Invocation persistence and activation review

`guardoath/invocations/store.py` implements metadata-only durable admission,
idempotency, receipts and conservative dispatch quotas. It does not provision
AWS infrastructure, configure provider credentials or enable hosted model calls.
The optional `deploy/aws/invocation-store.yaml` is separate from the active site
stack and defaults to creating no resources. Local tests use a real shared
SQLite file and a transactional DynamoDB double; these are not live DynamoDB or
paid-provider acceptance evidence.

## Store contract

Both `SQLiteInvocationStore(path, signing_key)` and
`DynamoDBInvocationStore(table_name, client, signing_key)` expose:

| Operation | Result and authority |
| --- | --- |
| `make_fingerprint(application_id, canonical_request_bytes)` | Stable tenant-scoped HMAC; request bytes are not retained. Do not add the current policy version to the canonical request. |
| `admit(receipt, fingerprint)` | `AdmissionResult(receipt, created)`. The unsigned initial receipt uses sequence zero; the accepted receipt starts at one. A duplicate returns its original receipt and `created=False`. |
| `advance(expected_receipt, next_receipt)` | Conditional state change plus immutable event. The caller copies the current sequence/digest; the store increments and signs them. |
| `reserve_dispatch(expected_receipt, next_receipt, quota)` | Atomically records intent, advances the head and reserves the connection's monthly quota. Only a successful return authorizes one adapter attempt. |
| `get(application_id, interaction_id)` | Tenant-bound receipt with its complete signed event chain verified. |
| `lookup_request(application_id, request_id)` | Receipt-only recovery if the original caller never learned the interaction ID. It cannot authorize a new attempt. |
| `recent(application_id, limit=20)` | Up to twenty verified receipts, ordered by their server timestamp and interaction ID. |

`DispatchQuota` contains `monthly_call_limit`, `monthly_output_token_limit` and
calendar `period` (`YYYY-MM`). Counters use the application, connection ID and
period. Changing a connection version or credential does not reset the allowance.
Reservations consume one call and the requested maximum output tokens. They stay
charged for success, rejection, withheld output and unknown outcomes. There is
no refund, usage settlement or dollar-budget claim. Reported usage is separate
evidence and remains unknown when the provider supplies none.

The [hosted acceptance verifier](model-invocation-live-acceptance.md) checks an
already-configured connection without spending model tokens by default. Its
explicit one-call mode sends a synthetic greeting once and verifies the same
durable receipt by request ID. It does not provision this store or grant approval
for the costs below.

The supported state graph is admission, input check, dispatch intent, observed
provider result and a terminal receipt. Blocked, simulated or incomplete input
cannot reach dispatch. Terminal receipts cannot be edited. The target, original
request, policy revision/digest and already-recorded evidence are immutable.
Approved output can be returned only after the final receipt write succeeds;
completion records preparation for HTTP delivery, not proof of client receipt.

## Failure and privacy rules

An ambiguous write raises `InvocationStoreUnavailable`, including a timeout
after a transaction may have committed. The store never converts a read-back
into fresh dispatch permission. Only a confirmed transaction-condition rejection
can retry a budget CAS; a network error cannot. A caller can recover metadata
with `lookup_request` or `get`, but cannot automatically resume an abandoned
execution. An intent with no outcome must be treated conservatively as unknown.

The receipt contains bounded identifiers, check evidence, frozen policy evidence,
target configuration, provider-reported usage and outcome metadata. It has no
prompt, answer, error-body, credential, complete policy or free-form metadata
field. The original POST can return checked text; subsequent calls return only
the receipt. Provider identifiers are restricted to identifier characters.

Each event is HMAC-signed over the previous event digest, request fingerprint and
canonical receipt. Reads validate the chain, state transitions and immutable
request mapping. Budget and recent-list rows are also signed. Keys must be stable
and at least 32 bytes; subkeys use separate domains. Replacing or losing the key
makes existing records unreadable and fails closed. There is no key-ring migration
implemented yet: preserve the key and records across deployments. Do not fix a
failed read by deleting records, clearing a budget or creating an empty store.

These signatures detect document/chain corruption under the signing-key trust
model. They do not establish an independently witnessed archive or detect every
privileged deletion or rollback of an entire database. Access to the signing key,
database administration and backups remains trusted. No automated data deletion,
TTL, abandoned-request takeover or reconciliation worker is implemented.

## Dedicated DynamoDB table

Use a separate composite-key table, with string `pk` and `sk`. Do not put these
records in the tenant directory or owner administration audit table.

| Key namespace | Contents |
| --- | --- |
| `T#application`, `R#request-hmac` | Immutable idempotency mapping |
| `T#application`, `I#interaction` | Current signed receipt and fingerprint |
| `T#application`, `E#interaction#sequence` | Append-only metadata event |
| `T#application`, `L#timestamp#interaction` | Signed recent-list pointer; avoids a secondary index |
| `T#application`, `B#connection#period` | Signed conservative reservation counter |

Admission writes the mapping, head, first event and list pointer in one
transaction. Dispatch writes the head, next event and budget in one transaction.
Other advances atomically update the head and append an event. Every Put has a
nonexistence or exact-prior-document condition. Reads are strongly consistent;
the event query is bounded by the sequence of the head that was read.

The injected SDK client must have bounded connect/read deadlines and a reviewed
retry policy that fits the request deadline. The implementation supplies a fresh
`ClientRequestToken` for each transaction attempt. AWS's ten-minute token window
is not the customer idempotency mechanism: the permanent request mapping is.
See [DynamoDB transaction API](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItems.html).

For this implementation, grant the runtime `dynamodb:GetItem` and
`dynamodb:Query` on the dedicated table ARN. Grant `dynamodb:PutItem` on that ARN
with `dynamodb:EnclosingOperation` equal to `TransactWriteItems`. Transaction
operations use their underlying item permissions; no Scan, DeleteItem,
UpdateItem, table-management or separate ConditionCheck action is required.
See [AWS transaction IAM guidance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis-iam.html).
Do not modify the active site's IAM/environment until this separate activation
review is approved.

## Concrete cost and capacity review

At AWS's published US East (N. Virginia) provisioned Standard rates checked on
2026-11-22, one RCU costs $0.00013/hour and one WCU costs $0.00065/hour. At 730 hours,
1 RCU + 1 WCU is **$0.5694/month**, and 5 + 5 is **$2.847/month**, before free-tier
eligibility, tax, storage and other services. The published Standard storage rate
above the allowance is $0.25/GB-month. AWS lists 25 RCU, 25 WCU and 25 GB free-tier
allowances, but account/organization eligibility and existing consumption have
not been verified for this new resource. Do not present this table as free.
Sources: [AWS DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/).

Provisioning one WCU is not one model invocation per second. A normal completed
interaction uses five transactions and thirteen item writes. AWS performs two
underlying writes per transaction item, with capacity also depending on item
size. Thus even the hypothetical all-items-under-1-KB case requires at least
26 write units per completed interaction, before retries or reads; actual
receipt sizes can require more. The template's 1/1 default is a review placeholder,
not tested production sizing. See
[AWS transaction capacity guidance](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/transaction-apis.html#transaction-capacity-handling).

The proposed 5/5 pilot also requires live capacity acceptance. Run pilot calls
sequentially with pauses; do not equate a low monthly infrastructure estimate
with fast request completion. Receipt and event documents can exceed 1 KB, and
transactional writes can exhaust the provisioned rate or available burst capacity.
The existing ten-second invocation deadline and one-attempt SDK policy remain
unchanged. In particular, throttling on the final receipt write withholds the
answer even if generation and validation succeeded. Preserve the request ID and
read its receipt; do not generate again to recover a withheld answer.

Before enabling, choose the region/table name, verify aggregate allowances and
cost limits, measure bounded receipt sizes and concurrency, provision sufficient
capacity, define retention/backup requirements, supply the stable key, and review
each provider/model's call and output-token quotas. Model fees, cross-region
transfer, credential storage, backups, extra logging and Lambda execution are
separate costs. No automatic scaling, indexes, streams, multi-region replicas,
TTL or backup service is enabled by the optional template. Metadata accumulates
without automatic deletion; broad rollout requires a retention design that
preserves idempotency/unknown-attempt evidence. No table or provider purchase was
made as part of this implementation.
