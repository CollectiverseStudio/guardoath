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
| pytest | 9.2.0 | https://pypi.org/pypi/pytest/json |
| actions/checkout | v7.0.1 | https://github.com/actions/checkout/releases |
| actions/setup-python | v7.1.0 | https://github.com/actions/setup-python/releases |

Local validation uses the available Python 3.12 runtime. Hosted CI covers Python
3.11, 3.12, 3.14, and the latest stable 3.15 line. Python 3.15.2 was the latest
stable Python release when checked at https://www.python.org/downloads/.

Presidio analyzer/anonymizer 2.2.366 were also checked but are not installed or
integrated yet. Their published Python constraint is >=3.11,<3.15. Review that
compatibility before adding them; do not claim every future adapter supports
every interpreter in the core API matrix.

This register records verification at a point in time. It does not imply that
versions stay current automatically. Supporting dependencies are resolved by pip;
a full reproducible lock and production vulnerability gate remain deployment work.

Starlette 1.10.0 recommends HTTPX2 for its test client. The test dependency follows
that supported path rather than retaining the deprecated HTTPX fallback.
