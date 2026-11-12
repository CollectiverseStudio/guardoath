"""Synthetic-only checks of live dashboard parity; never call production."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.error import URLError

import pytest

from tools.verify_live_dashboard import REQUIRED, verify, verify_release


BASE = "https://guardoath.com"


class Response:
    status = 200

    def __init__(self, url, body, resolved=None):
        self.url = resolved or url
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def geturl(self):
        return self.url

    def read(self, size):
        return self.body[:size]


def setup(tmp_path: Path):
    responses = {}
    tags = {}
    for name in sorted(REQUIRED):
        body = ("Synthetic " + name).encode()
        (tmp_path / name).write_bytes(body)
        tag = hashlib.sha256(body).hexdigest()[:12]
        tags[name] = tag
        responses[BASE + "/assets/" + name + "?v=" + tag] = body
    template = "<!doctype html><title>GuardOath</title>" + "".join(
        '<script src="/assets/' + name + '"></script>'
        for name in sorted(REQUIRED)
    )
    (tmp_path / "dashboard.html").write_text(template, encoding="utf-8")
    html = "<!doctype html><title>GuardOath</title>" + "".join(
        '<script src="/assets/' + name + "?v=" + tags[name] + '"></script>'
        for name in sorted(REQUIRED)
    )
    responses[BASE + "/dashboard"] = html.encode()
    return responses, tags


def opener(responses, *, redirect=None):
    def open_url(request, timeout):
        assert timeout == 10
        url = request.full_url
        if url not in responses:
            raise URLError("synthetic missing response")
        return Response(url, responses[url], resolved=redirect)
    return open_url


def test_all_versioned_production_assets_match_exact_local_source(tmp_path):
    responses, _ = setup(tmp_path)
    result = verify(BASE, tmp_path, opener(responses))
    assert result == {
        "dashboard_ui_matches_local_source": True,
        "verified_asset_count": len(REQUIRED),
        "expected_asset_count": len(REQUIRED),
        "issues": [],
        "backend_deployment_sha_verified": False,
        "production_tenant_api_verified": False,
        "mutated_records": 0,
    }




def test_changed_dashboard_markup_blocks_release_even_with_matching_js_css_and_backend(tmp_path):
    """A stale form/control is a release mismatch even when every asset hash matches."""
    responses, _ = setup(tmp_path)
    responses[BASE + "/healthz"] = ('{"status":"ok","source_sha":"' + SHA + '"}').encode()
    responses[BASE + "/dashboard"] = responses[BASE + "/dashboard"].replace(
        b"<title>GuardOath</title>", b"<title>GuardOath stale form</title>"
    )
    report = verify_release(BASE, SHA, tmp_path, opener(responses))
    assert "dashboard_html_content_mismatch" in report["issues"]
    assert report["verified_asset_count"] == len(REQUIRED)
    assert report["backend_deployment_sha_verified"] is True
    assert report["dashboard_ui_matches_local_source"] is False
    assert report["release_source_parity_verified"] is False
    assert report["mutated_records"] == 0


def test_missing_local_dashboard_template_cannot_certify_browser_source(tmp_path):
    responses, _ = setup(tmp_path)
    (tmp_path / "dashboard.html").unlink()
    report = verify(BASE, tmp_path, opener(responses))
    assert "source_dashboard_html_unavailable:FileNotFoundError" in report["issues"]
    assert report["dashboard_ui_matches_local_source"] is False
    assert report["verified_asset_count"] == len(REQUIRED)
    assert report["mutated_records"] == 0


def test_stale_cloudfront_asset_never_certifies_new_source(tmp_path):
    responses, tags = setup(tmp_path)
    key = BASE + "/assets/dashboard.js?v=" + tags["dashboard.js"]
    responses[key] = b"STALE JAVASCRIPT"
    result = verify(BASE, tmp_path, opener(responses))
    assert result["dashboard_ui_matches_local_source"] is False
    assert "asset_content_mismatch:dashboard.js" in result["issues"]
    assert result["mutated_records"] == 0


def test_dashboard_missing_new_editor_never_passes(tmp_path):
    responses, _ = setup(tmp_path)
    responses[BASE + "/dashboard"] = responses[BASE + "/dashboard"].replace(
        b'/assets/conversion_editor.js?v=', b'/assets/removed_editor_file.js?v=')
    result = verify(BASE, tmp_path, opener(responses))
    assert result["dashboard_ui_matches_local_source"] is False
    assert "missing_dashboard_assets:conversion_editor.js" in result["issues"][0]


def test_missing_tenant_stage_tabs_never_certifies_production_dashboard(tmp_path):
    """A release missing the Input/Output stage navigation is not accepted."""
    responses, tags = setup(tmp_path)
    assert "tenant_stage_tabs.js" in REQUIRED
    original = ("/assets/tenant_stage_tabs.js?v=" + tags["tenant_stage_tabs.js"]).encode()
    responses[BASE + "/dashboard"] = responses[BASE + "/dashboard"].replace(
        original, b"/assets/missing_tenant_tabs.js?v=" + tags["tenant_stage_tabs.js"].encode()
    )
    result = verify(BASE, tmp_path, opener(responses))
    assert result["dashboard_ui_matches_local_source"] is False
    assert any("tenant_stage_tabs.js" in item for item in result["issues"])
    assert result["mutated_records"] == 0


def test_missing_wizard_asset_never_certifies_production_dashboard(tmp_path):
    """A newly shipped wizard is mandatory in exact-source asset attestation."""
    responses, tags = setup(tmp_path)
    assert "conversation_wizard.js" in REQUIRED
    responses[BASE + "/dashboard"] = responses[BASE + "/dashboard"].replace(
        ("/assets/conversation_wizard.js?v=" + tags["conversation_wizard.js"]).encode(),
        b"/assets/removed_wizard.js?v=" + tags["conversation_wizard.js"].encode(),
    )
    result = verify(BASE, tmp_path, opener(responses))
    assert result["dashboard_ui_matches_local_source"] is False
    assert any("conversation_wizard.js" in item for item in result["issues"])
    assert result["mutated_records"] == 0


def test_unversioned_assets_and_wrong_version_tag_are_rejected(tmp_path):
    responses, tags = setup(tmp_path)
    tag = tags["dashboard.js"]
    responses[BASE + "/dashboard"] = responses[BASE + "/dashboard"].replace(
        ("/assets/dashboard.js?v=" + tag).encode(), b"/assets/dashboard.js")
    result = verify(BASE, tmp_path, opener(responses))
    assert not result["dashboard_ui_matches_local_source"]
    assert "unversioned_asset_link" in result["issues"]
    assert any(i.startswith("missing_dashboard_assets:") for i in result["issues"])

    responses, tags = setup(tmp_path)
    old = tags["dashboard.js"]
    responses[BASE + "/dashboard"] = responses[BASE + "/dashboard"].replace(
        ("/assets/dashboard.js?v=" + old).encode(), b"/assets/dashboard.js?v=000000000000")
    result = verify(BASE, tmp_path, opener(responses))
    assert "asset_version_mismatch:dashboard.js" in result["issues"]


def test_redirect_to_other_origin_is_never_treated_as_deployed_asset(tmp_path):
    responses, _ = setup(tmp_path)
    with pytest.raises(ValueError, match="redirect changed"):
        verify(BASE, tmp_path, opener(responses, redirect="https://other.example.com/login"))


@pytest.mark.parametrize("origin", [
    "http://guardoath.com",
    "https://guardoath.com/some/path",
    "https://guardoath.com?token=private",
    "https://user:password@guardoath.com",
])
def test_only_bare_secure_origin_is_allowed(tmp_path, origin):
    with pytest.raises(ValueError):
        verify(origin, tmp_path, opener({}))


def test_unreachable_live_site_fails_closed(tmp_path):
    responses, _ = setup(tmp_path)
    responses.pop(BASE + "/dashboard")
    with pytest.raises(URLError):
        verify(BASE, tmp_path, opener(responses))


SHA = "a" * 40


def test_live_release_requires_matching_dashboard_and_embedded_lambda_sha(tmp_path):
    responses, _ = setup(tmp_path)
    responses[BASE + "/healthz"] = ('{"status":"ok","source_sha":"' + SHA + '"}').encode()
    report = verify_release(BASE, SHA, tmp_path, opener(responses))
    assert report["release_source_parity_verified"] is True
    assert report["dashboard_ui_matches_local_source"] is True
    assert report["backend_deployment_sha_verified"] is True
    assert report["live_source_sha"] == SHA
    assert report["production_tenant_api_verified"] is False


@pytest.mark.parametrize("health", [
    b'{"status":"ok","source_sha":null}',
    b'{"status":"ok","source_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}',
    b'{"status":"degraded","source_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
    b'{"status":"ok","source_sha":"unverified"}',
    b'{}',
    b'not-json',
])
def test_health_missing_wrong_or_invalid_sha_never_claims_deployed_source(tmp_path, health):
    responses, _ = setup(tmp_path)
    responses[BASE + "/healthz"] = health
    report = verify_release(BASE, SHA, tmp_path, opener(responses))
    assert report["dashboard_ui_matches_local_source"] is True
    assert report["backend_deployment_sha_verified"] is False
    assert report["release_source_parity_verified"] is False
    assert any(issue.startswith("backend_") for issue in report["issues"])



@pytest.mark.parametrize("health", [
    b"null",
    b"[]",
    b'"healthy"',
    b'42',
    b'{"status":"degraded","status":"ok","source_sha":"' + SHA.encode() + b'"}',
    b'{"status":"ok","source_sha":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","source_sha":"' + SHA.encode() + b'"}',
])
def test_ambiguous_or_non_object_health_cannot_attest_release(tmp_path, health):
    """Duplicate keys and scalar health payloads must fail closed, not crash."""
    responses, _ = setup(tmp_path)
    responses[BASE + "/healthz"] = health
    report = verify_release(BASE, SHA, tmp_path, opener(responses))
    assert report["dashboard_ui_matches_local_source"] is True
    assert report["backend_deployment_sha_verified"] is False
    assert report["release_source_parity_verified"] is False
    assert any(issue.startswith("backend_health_unavailable:ValueError") for issue in report["issues"])
    assert report["mutated_records"] == 0


def test_stale_asset_rejects_release_even_when_backend_sha_matches(tmp_path):
    responses, tags = setup(tmp_path)
    responses[BASE + "/healthz"] = ('{"status":"ok","source_sha":"' + SHA + '"}').encode()
    responses[BASE + "/assets/dashboard.js?v=" + tags["dashboard.js"]] = b"stale"
    report = verify_release(BASE, SHA, tmp_path, opener(responses))
    assert report["backend_deployment_sha_verified"] is True
    assert report["dashboard_ui_matches_local_source"] is False
    assert report["release_source_parity_verified"] is False


def test_unavailable_health_never_claims_backend_sha(tmp_path):
    responses, _ = setup(tmp_path)
    report = verify_release(BASE, SHA, tmp_path, opener(responses))
    assert report["release_source_parity_verified"] is False
    assert any(issue.startswith("backend_health_unavailable:") for issue in report["issues"])


def test_verifier_inventory_covers_every_real_dashboard_script_and_style():
    """New UI modules must be included before a release can be certified."""
    import re
    from tools.verify_live_dashboard import ROOT
    markup = (ROOT / "guardoath/static/dashboard.html").read_text(encoding="utf-8")
    linked = set(re.findall(
        r'(?:src|href)="/assets/([a-z0-9_-]+\.(?:js|css))"', markup))
    assert linked == REQUIRED
