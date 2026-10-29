# Verified dependency versions

Checked against PyPI and the maintainers' GitHub releases on October 27, 2026.
These are stable releases, not preview versions. Direct dependencies are pinned
so upgrades are deliberate and tested.

| Dependency | Version | Source |
| --- | --- | --- |
| FastAPI | 0.162.0 | https://pypi.org/pypi/fastapi/json |
| Pydantic | 2.14.1 | https://pypi.org/pypi/pydantic/json |
| Uvicorn | 0.54.0 | https://pypi.org/pypi/uvicorn/json |
| jsonschema | 4.26.0 | https://pypi.org/pypi/jsonschema/json |
| HTTPX2 | 2.16.0 | https://pypi.org/pypi/httpx2/json |
| Starlette | 1.10.0 | https://pypi.org/pypi/starlette/json |
| email-validator | 2.3.0 | https://pypi.org/pypi/email-validator/json |
| rfc3339-validator | 0.1.4 | https://pypi.org/pypi/rfc3339-validator/json |
| detect-secrets | 1.5.0 | https://pypi.org/pypi/detect-secrets/json |
| regex | 2026.9.29 | https://pypi.org/pypi/regex/json |
| pytest | 9.2.0 | https://pypi.org/pypi/pytest/json |
| actions/checkout | v7.0.1 | https://github.com/actions/checkout/releases |
| actions/setup-python | v7.1.0 | https://github.com/actions/setup-python/releases |
| actions/setup-node | v7.1.0 | https://github.com/actions/setup-node/releases |
| @playwright/test | 1.64.1 | https://registry.npmjs.org/@playwright/test/latest |

Local validation uses the available Python 3.12 runtime. Hosted CI covers Python
3.11, 3.12, 3.14, and the latest stable 3.15 line. Python 3.15.2 was the latest
stable Python release when checked at https://www.python.org/downloads/.

Presidio analyzer 2.2.366 is integrated as an optional `pii` extra. Its published
Python constraint is >=3.11,<3.15, so the core service still tests Python 3.15
while the optional adapter has separate 3.12 and 3.14 CI jobs. Enabling PII on
3.15 fails explicitly at startup. No NLP model weights are downloaded or bundled.
Presidio anonymizer 2.2.366 was checked but is not integrated. Valinator applies
bounded span replacements from its local recognizers and reruns active checks;
this avoids adding another dependency for the current limited transformation.

Presidio analyzer source and license: https://github.com/data-privacy-stack/presidio
The analyzer package declares the MIT license. Its dependencies retain their own
licenses. This integration reuses installed packages rather than vendoring them.

This register records verification at a point in time. It does not imply that
versions stay current automatically. Supporting dependencies are resolved by pip;
a full reproducible lock and production vulnerability gate remain deployment work.

Starlette 1.10.0 recommends HTTPX2 for its test client. The test dependency follows
that supported path rather than retaining the deprecated HTTPX fallback.

detect-secrets 1.5.0 and regex 2026.9.29 were checked as current stable releases
before adding the local secret adapter. detect-secrets is Apache-2.0 licensed:
https://github.com/Yelp/detect-secrets. The adapter reuses selected expressions
and local JWT structure validation; it does not call verification, scan files,
create baselines, or honor user-supplied suppressions. Installed package use does
not vendor the upstream source into this repository.

The optional `azure` extra reuses the existing pinned HTTPX2 2.16.0 dependency;
no provider SDK/model download is added. Its request contract was checked on
October 28, 2026 against Microsoft's stable Prompt Shields API `2024-09-01`:
https://learn.microsoft.com/en-us/rest/api/contentsafety/text-operations/shield-prompt?view=rest-contentsafety-2024-09-01.
API version pinning does not pin Azure's underlying model. Azure is optional,
requires explicit activation and customer-controlled credentials, and may charge
for requests. Offline test transports do not establish live service accuracy.

The Secrets group batch reuses additional regex-based plugin expressions from
installed detect-secrets 1.5.0 (GitLab, Slack, SendGrid, Twilio, npm, PyPI, Azure
Storage, Basic Auth, Discord and Telegram). No plugin verification or network
calls are invoked and no upstream source is vendored. Quoted-assignment,
configured literal-term and bounded decoding orchestration are local Valinator
code. The new GitHub fine-grained rule is explicitly a prefix-based candidate,
not a documented full-token validator; GitHub's prefix reference is
https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/about-authentication-to-github.

The Privacy group batch adds no dependencies and makes no new upstream-version
claim: installed Presidio remains the optional email/card/SSN adapter; local
Valinator field heuristics reuse the pinned regex package and Python calendar
validation. No NLP model, phone numbering-plan database, patient/insurance/bank
lookup or external service is introduced.

Structure batch: no dependency/version additions. The existing jsonschema 4.26.0,
email-validator 2.3.0 and rfc3339-validator 0.1.4 are reused. Explicit time/date-time
predicates replace the generic draft-3 time fallback, enforce offsets and reject
whitespace. Leap seconds remain unsupported. Primary format contract:
https://json-schema.org/draft/2020-12/json-schema-validation, section 7.3.1.
This batch makes no new claim about upstream release freshness.

Business batch: no dependencies or version pins added. After the existing strict
JSON validation guard, the comparison checker decodes the same canonical text
with a standard-library JSON parse_float callback that returns Decimal after an
explicit exponent bound (absolute encoded exponent at most 1,000,000). It retains token precision
for comparisons only, without changing schema or transformation numeric parsing.
Unrepresentable exponents are rejected. Python reference:
https://docs.python.org/3/library/json.html (parse_float).

The supported local text-security batch adds no dependencies or detection models.
It reuses Python standard-library base64 (validate=True), hex byte decoding and
UTF-8 decoding with the existing bounded JSON parser. Fixed diagnostic classes
are Valinator logic; no NLP/Unicode database/model or provider is consulted.
No canonical Base64 padding-bit enforcement is claimed. Python reference:
https://docs.python.org/3/library/base64.html. Upstream pins remain unchanged.


Version 0.21 optional validation adapter: boto3 1.43.119 and botocore 1.43.121
were verified available and installed for the isolated `aws` extra; the portable
core never imports them when no provider is selected. This extra is for a chosen
validation service, not a hosting requirement. The resolver selected botocore
1.43.121, pinned for adapter compatibility tests. This is a point-in-time choice,
not automatic updates. Primary reference: https://pypi.org/project/boto3/.
No paid provider calls, deployment dependencies or infrastructure are added.


Version 0.22 matched-outcome checks add no dependencies. They reuse the existing
bounded JSON parser, exact decimal-token conversion, Pydantic policy contracts
and engine. The algorithm is a limited Valinator consistency check, not an
implementation or invocation of Fairlearn metrics. Fairlearn's primary fairness
and construct-validity documentation informs the documented limitations:
https://fairlearn.org/main/user_guide/fairness_in_machine_learning.html.
No new upstream release or model-accuracy claim is made.


The same version's evidence checker uses existing strict JSON/Pydantic contracts
and standard-library regex/string matching, with a trusted local source catalog.
The proposed-action checker reuses the existing jsonschema 4.26.0 validator and
supported format predicates. It adds an explicit lossy numeric-token rejection
before schema evaluation, not a new arithmetic engine. No new packages, model
weights, executable tools, reference lookups or cloud services are introduced.


Version 0.23 encoded PII reuses the existing optional Presidio patterns, strict
JSON parser and bounded standard-library Base64/hex UTF-8 decoder. It adds no
packages or new upstream-version claim, and performs no verification/network/model
calls. The wrapper preserves its underlying PII checker version in metadata.
