"""Read-only production dashboard parity check against the local GitHub checkout.

From the repository root:
    python -m tools.verify_live_dashboard --url https://guardoath.com

Checks the deployed dashboard's versioned JS/CSS links and full response bytes
against the local checkout. No AWS credentials, tenant API key, writes, or
customer data are required. Backend Lambda code parity is NOT established.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "guardoath" / "static"
LINK = re.compile(r"""(?:src|href)=["']/assets/([a-z0-9_-]+\.(?:js|css))\?v=([0-9a-f]{12})["']""")
REQUIRED = frozenset({
    "dashboard.css", "sigma.css", "dashboard.js", "sigma_tenant.js",
    "comparison_editor.js", "arithmetic_editor.js",
    "identity_editor.js", "conversion_editor.js", "statistical_drift_editor.js",
})
MAX_HTML_BYTES = 256_000
MAX_ASSET_BYTES = 2_000_000


def _fetch(url: str, limit: int, open_url=urlopen) -> bytes:
    # Do not follow arbitrary redirects to another host (including login URLs).
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise ValueError("Live verifier requires an HTTPS URL without credentials")
    with open_url(Request(url, headers={"Accept": "text/html,application/javascript,text/css"}), timeout=10) as response:
        if getattr(response, "status", 200) != 200:
            raise ValueError("Production response is not HTTP 200")
        resolved = urlsplit(response.geturl())
        if resolved.scheme != "https" or resolved.netloc != parts.netloc:
            raise ValueError("Production redirect changed the origin")
        payload = response.read(limit + 1)
        if len(payload) > limit:
            raise ValueError("Production response exceeds bounded verification size")
        return payload


def verify(base_url: str, static_root: Path = ASSETS, open_url=urlopen) -> dict:
    base = base_url.rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("Expected a bare HTTPS origin, e.g. https://guardoath.com")
    html = _fetch(base + "/dashboard", MAX_HTML_BYTES, open_url).decode("utf-8")
    pairs = LINK.findall(html)
    linked = dict(pairs)
    failures = []
    if len(pairs) != len(linked):
        failures.append("duplicate_versioned_asset_link")
    missing = sorted(REQUIRED - linked.keys())
    if missing:
        failures.append("missing_dashboard_assets:" + ",".join(missing))
    # Non-versioned local links risk a stale CloudFront response.
    if re.search(r"""(?:src|href)=["']/assets/[a-z0-9_-]+\.(?:js|css)["']""", html):
        failures.append("unversioned_asset_link")
    checked = []
    for name, served_tag in sorted(linked.items()):
        path = static_root / name
        if not path.is_file():
            failures.append("source_asset_missing:" + name)
            continue
        expected = path.read_bytes()
        if len(expected) > MAX_ASSET_BYTES:
            failures.append("source_asset_too_large:" + name)
            continue
        digest = hashlib.sha256(expected).hexdigest()[:12]
        if digest != served_tag:
            failures.append("asset_version_mismatch:" + name)
        try:
            actual = _fetch(base + "/assets/" + name + "?v=" + served_tag,
                            MAX_ASSET_BYTES, open_url)
        except (HTTPError, URLError, OSError, ValueError) as exc:
            failures.append("asset_unavailable:" + name + ":" + type(exc).__name__)
            continue
        if actual != expected:
            failures.append("asset_content_mismatch:" + name)
        else:
            checked.append(name)
    return {
        "dashboard_ui_matches_local_source": not failures,
        "verified_asset_count": len(checked),
        "expected_asset_count": len(linked),
        "issues": failures,
        "backend_deployment_sha_verified": False,
        "production_tenant_api_verified": False,
        "mutated_records": 0,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://guardoath.com")
    parser.add_argument("--static-root", type=Path, default=ASSETS)
    args = parser.parse_args(argv)
    try:
        result = verify(args.url, args.static_root)
    except (HTTPError, URLError, OSError, UnicodeError, ValueError) as exc:
        result = {
            "dashboard_ui_matches_local_source": False,
            "verified_asset_count": 0,
            "issues": ["live_verification_incomplete:" + type(exc).__name__],
            "backend_deployment_sha_verified": False,
            "production_tenant_api_verified": False,
            "mutated_records": 0,
        }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["dashboard_ui_matches_local_source"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
