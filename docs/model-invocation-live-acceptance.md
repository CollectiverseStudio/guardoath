# Hosted model acceptance

`python -m tools.verify_model_invocation` checks the live GuardOath model API
without changing infrastructure, tenant policy, subscription state or provider
configuration. Its default mode sends only a public health GET and an
authenticated connection-catalog GET. No prompt or generation POST is sent.

Supply a customer API key through the process environment as
`GUARDOATH_API_KEY`; do not put the key in command-line arguments or a committed
file. Use the actual application ID that owns that credential:

```sh
python -m tools.verify_model_invocation --application-id YOUR_APPLICATION_ID
```

An optional `--expected-sha` requires the live public build identity to equal
the exact accepted 40-character source SHA. A mismatch stops before catalog
inspection. Read-only success means the authenticated catalog declares at least
one connection ready. It does not prove credentials, model access, latency,
provider billing, dollar budgets or a successful generation. An unconfigured
runtime, public demo, unpaid account or observe-only policy remains unavailable.

## One explicitly approved synthetic call

After storage, stable signing key, exact provider/model binding, account access
and costs have been approved and activated, use the approved catalog aliases.
Save a unique request ID for this logical operation before invoking:

```sh
python -m tools.verify_model_invocation \
  --application-id YOUR_APPLICATION_ID \
  --expected-sha ACCEPTED_40_CHARACTER_SOURCE_SHA \
  --invoke --connection-id APPROVED_CONNECTION --model-id APPROVED_MODEL \
  --request-id SAVED_LOGICAL_REQUEST_ID --max-output-tokens 128
```

`--invoke` permits one potentially billable POST, using a fixed harmless
synthetic greeting request. The tool accepts 1–256 output tokens, additionally
bounded by the approved model ceiling and connection token allowance. It checks
the existing request-ID mapping first. Any existing receipt or an unavailable
mapping stops generation; a request ID is never silently replaced.

The transport fixes the origin to `https://guardoath.com`, uses TLS verification,
does not follow redirects, bounds JSON response reads and sends the SHA-256 of
the exact transmitted POST bytes in `x-amz-content-sha256`. There are no automatic
HTTP or provider retries in this tool. An upstream timeout does not prove that
the provider stopped or that no charge occurred.

After its single POST attempt the tool makes a receipt-only recovery GET. Success
requires the original response to be unreplayed, contain nonempty checked text,
match the authenticated application, saved request and selected provider target,
record complete non-simulated input/output checks with no Block decision, and
match the whole durable receipt returned by recovery. The server verifies its
signed ledger; the tool correlates the authenticated responses and does not
independently possess the signing key.

If a POST result is lost but recovery finds a completed receipt, the report
remains unsuccessful: the original answer was not observed by this run. Recovery
returns no answer, never regenerates it and never resumes an uncertain attempt.
Keep the same request ID and investigate the receipt before creating a separate
logical operation. A subsequent invocation with that existing ID stops at the
read-only lookup.

## Report and limits

Exit code 0 means the requested read-only or one-call verification passed; exit
code 2 means it did not. The JSON report contains booleans, fixed issue codes and
bounded catalog/receipt status values. It omits credentials, application/request
IDs, provider identifiers, prompt, answer and raw exception bodies. A reported
`post_attempted` is an HTTP attempt, not proof that a model ran.

This is one focused operational check. It supplies part of LIVE-13 in the
[numbered production acceptance manifest](production-live-acceptance.md).
It does not complete the 15 production acceptance checks, demonstrate broad safety coverage, prove tenant isolation
across live accounts, test agents/tools/streaming, verify financial charges or
authorize resource changes. It requires an already approved customer connection;
the tool does not create one or enable provider spending.

See [implemented API and enforcement](guarded-model-invocations.md) and
[storage/capacity activation review](invocation-storage.md).
