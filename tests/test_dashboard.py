import pytest
from fastapi.testclient import TestClient

from guardoath.app import create_app
from guardoath.checkers import default_checkers
from guardoath.engine import Engine
from guardoath.models import Policy, Rule


def test_dashboard_and_local_assets_load():
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Check playground" in response.text
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "no-store"
    for asset in ["dashboard.css", "dashboard.js"]:
        assert client.get("/assets/" + asset).status_code == 200


def test_dashboard_metadata_requires_auth_and_is_application_scoped():
    policies = [Policy(application_id="demo", version="public-v1", rules=(Rule(checker_id="safety"),)),
                Policy(application_id="other", version="private-v1", rules=())]
    client = TestClient(create_app(engine=Engine(policies, default_checkers()), credentials={"test-key": "demo"}))
    assert client.get("/v1/applications/current").status_code == 401
    response = client.get("/v1/applications/current", headers={"Authorization": "Bearer test-key"})
    assert response.status_code == 200
    assert "private-v1" not in response.text
    assert response.json()["policies"][0]["checks"][0]["simulated"] is True
    assert "test-key" not in response.text


def conversation_client(enable_pii):
    from pathlib import Path
    from guardoath.config import load_policies
    engine = Engine(load_policies(Path(__file__).resolve().parents[1] / 'policies/playground.json'),
                    default_checkers(enable_pii=enable_pii))
    return TestClient(create_app(engine=engine, credentials={'test-key': 'demo'}))


def test_conversation_profile_cannot_pass_without_its_required_pii_adapter():
    client = conversation_client(False)
    response = client.post('/v1/checks/input', headers={'Authorization': 'Bearer test-key'},
                           json={'application_id': 'demo', 'policy_version': 'conversation-v1',
                                 'content': 'Explain returns.'})
    result = response.json()
    assert response.status_code == 200
    assert result['evaluation_status'] == 'incomplete'
    assert {c['checker_id'] for c in result['checker_results']} == {'pii', 'secrets', 'text_security', 'encoded_pii'}


def test_conversation_profile_runs_combined_checks_on_both_stages():
    import pytest
    pytest.importorskip('tldextract')
    pytest.importorskip('phonenumbers')
    client = conversation_client(True)
    for stage in ['input', 'output']:
        for content, flagged in [('Explain returns.', None), ('Email jane@example.com', 'pii'),
                                 ('password = "synthetic-example!42"', 'secrets'),
                                 ('Hidden\u200bcharacter', 'text_security')]:
            response = client.post('/v1/checks/' + stage, headers={'Authorization': 'Bearer test-key'},
                                   json={'application_id': 'demo', 'policy_version': 'conversation-v1',
                                         'content': content})
            result = response.json()
            assert response.status_code == 200
            assert result['evaluation_status'] == 'complete'
            checks = {c['checker_id']: c for c in result['checker_results']}
            assert set(checks) == {'pii', 'secrets', 'text_security', 'encoded_pii'}
            assert all(not c['simulated'] and not c['external'] for c in checks.values())
            if flagged:
                assert checks[flagged]['status'] == 'flagged'
                assert result['recommended_action'] == ('flag' if stage == 'input' else 'block')
            else:
                assert all(c['status'] == 'passed' for c in checks.values())
                assert result['recommended_action'] == 'allow'


def test_labeled_sensitive_data_setting_is_strict_and_requires_a_pii_rule():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Policy(application_id='demo',version='v1',rules=(),detect_labeled_sensitive_data='true')
    with pytest.raises(ValueError,match='require a pii'):
        Engine([Policy(application_id='demo',version='v1',rules=(),detect_labeled_sensitive_data=True)],{})


def test_dashboard_shows_the_demo_video():
    from pathlib import Path
    import tomllib
    root = Path(__file__).resolve().parents[1]
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    page = client.get("/dashboard").text
    assert '<video id="demo-video"' in page and 'src="/assets/guardoath_ai_final.mp4"' in page
    size = (root / "guardoath" / "static" / "guardoath_ai_final.mp4").stat().st_size
    # The video is bigger than one part (the hosted service cannot return it in one response), so a
    # request without a Range header is told to ask for parts.
    from guardoath.app import MEDIA_PART_BYTES
    assert size > MEDIA_PART_BYTES
    response = client.get("/assets/guardoath_ai_final.mp4")
    assert response.status_code == 416 and response.headers["content-range"] == f"bytes */{size}"
    assert response.headers["accept-ranges"] == "bytes"
    part = client.get("/assets/guardoath_ai_final.mp4", headers={"Range": "bytes=0-1023"})
    assert part.status_code == 206 and len(part.content) == 1024
    assert part.headers["content-range"] == f"bytes 0-1023/{size}"
    # The installed package (and the hosted build) must include the video.
    package_data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]
    assert "static/*.mp4" in package_data["guardoath"]


def test_dashboard_shows_the_logo_motto_and_product_message():
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    page = client.get("/dashboard").text
    assert 'class="logo" src="/assets/guardoath-shield.png"' in page
    assert ">G</span>" not in page
    for text in ["Building Confidence Into AI.", "Validate Inputs. Protect Data. Govern AI Actions.",
                 "AI is powerful. Every interaction deserves validation.",
                 "Helping build a safer future for humans and AI.", "DEVELOPMENT PREVIEW"]:
        assert text in page
    logo = client.get("/assets/guardoath-shield.png")
    assert logo.status_code == 200 and logo.headers["content-type"] == "image/png"
    assert logo.content.startswith(b"\x89PNG\r\n\x1a\n")
    # The video sits right after the hero, ahead of the playground.
    assert page.index('id="hero-title"') < page.index('id="demo-video"') < page.index('id="connect-title"')


def test_conversation_box_holds_every_step_and_advanced_is_open():
    page = TestClient(create_app(credentials={"test-key": "demo"})).get("/dashboard").text
    start = page.index('<section class="workspace panel" aria-labelledby="conversation-title">')
    end = page.index("</section><footer>")
    box = page[start:end]
    for marker in ['id="advanced" open', 'id="input-title"', 'id="input-check-title"', 'id="output-title"',
                   'id="output-check-title"', 'id="answer-title"', 'id="policy-help"']:
        assert marker in box, marker
    assert box.count('class="panel flow-step"') == 4


def test_tenant_panel_explains_every_action():
    page = TestClient(create_app(credentials={"test-key": "demo"})).get("/dashboard").text
    for term in ["<dt>Off</dt>", "<dt>Block</dt>", "<dt>Flag</dt>", "<dt>Allow</dt>", "<dt>Redact</dt>"]:
        assert term in page


def test_every_check_and_profile_has_an_explanation_and_a_real_guide_link():
    import json
    import re
    from pathlib import Path
    from guardoath.tenant_record import CHECK_IDS
    root = Path(__file__).resolve().parents[1]
    script = (root / "guardoath" / "static" / "dashboard.js").read_text(encoding="utf-8")
    checks = json.loads(re.search(r"^const checkHelp = (.*);$", script, re.M).group(1))
    profiles = json.loads(re.search(r"^const profileHelp = (.*);$", script, re.M).group(1))
    assert set(checks) == set(CHECK_IDS)
    versions = {p["version"] for p in json.loads((root / "policies" / "playground.json").read_text(encoding="utf-8"))["policies"]}
    assert set(profiles) == versions | {"tenant"}
    # The links go to the developer guide on guardoath.com (it was the user guide on GitHub until 2026-10-30).
    guide = (root / "guardoath" / "static" / "guide.html").read_text(encoding="utf-8")
    anchors = set(re.findall(r'id="([^"]+)"', guide))
    for check_id, help in checks.items():
        assert help["what"] and help["why"] and help["anchor"] == "check-" + check_id and help["anchor"] in anchors, help
    for version, help in profiles.items():
        assert help["what"] and help["why"] and help["anchor"] == "profile-" + version and help["anchor"] in anchors, help


def test_proposed_answer_is_locked_until_the_input_test_passes():
    page = TestClient(create_app(credentials={"test-key": "demo"})).get("/dashboard").text
    box = page[page.index('<textarea id="output-content"'):]
    box = box[:box.index("</textarea>")]
    assert " readonly " in box and "the input test has not run" in box
    assert 'id="batch-run"' in page
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "guardoath" / "static" / "dashboard.js").read_text(encoding="utf-8")
    for text in ["the input test has not run", "the input test did not pass", "The input test passed", "Prepared example"]:
        assert text in script, text
    assert "function outputLocked() { return !preparedRun() && !canProceed(inputResult); }" in script


def test_tenant_panel_has_a_default_profile_picker_and_a_profile_note():
    page = TestClient(create_app(credentials={"test-key": "demo"})).get("/dashboard").text
    panel = page[page.index('id="tenant-panel"'):]
    panel = panel[:panel.index("</section>")]
    for marker in ['id="tenant-default-profile"', 'id="tenant-default-save"', 'id="tenant-default-note"',
                   'id="tenant-profile-note"', 'id="tenant-checks"']:
        assert marker in panel, marker
    assert panel.index('id="tenant-default-profile"') < panel.index('id="tenant-checks"')


# The demo video is served in parts (guardoath.app.media_part)

VIDEO = "/assets/guardoath_ai_final.mp4"


def video_bytes():
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "guardoath" / "static" / "guardoath_ai_final.mp4").read_bytes()


def test_the_video_reassembles_exactly_from_its_parts():
    from guardoath.app import MEDIA_PART_BYTES
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    expected, received, start = video_bytes(), b"", 0
    while start < len(expected):
        part = client.get(VIDEO, headers={"Range": f"bytes={start}-"})
        assert part.status_code == 206 and part.headers["content-type"].startswith("video/mp4")
        assert 0 < len(part.content) <= MEDIA_PART_BYTES
        end = start + len(part.content) - 1
        assert part.headers["content-range"] == f"bytes {start}-{end}/{len(expected)}"
        assert int(part.headers["content-length"]) == len(part.content)
        received += part.content
        start = end + 1
    assert received == expected


@pytest.mark.parametrize("header, status", [
    ("bytes=-100", 206), ("bytes=10-19", 206), ("bytes=0-0", 206),
    ("bytes={size}-", 416), ("bytes=20-10", 416), ("bytes=0-1,5-9", 416), ("items=0-1", 416), ("bytes=-", 416),
    ("bytes=x-1", 416),
])
def test_video_range_requests(header, status):
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    data = video_bytes()
    response = client.get(VIDEO, headers={"Range": header.replace("{size}", str(len(data)))})
    assert response.status_code == status
    if status == 206:
        first, last = (int(n) for n in response.headers["content-range"].split(" ")[1].split("/")[0].split("-"))
        assert response.content == data[first:last + 1]
        if header == "bytes=-100":
            assert response.content == data[-100:]
    else:
        assert response.headers["content-range"] == f"bytes */{len(data)}"


def test_a_video_head_request_sends_headers_only():
    client = TestClient(create_app(credentials={"test-key": "demo"}))
    response = client.head(VIDEO, headers={"Range": "bytes=0-1023"})
    assert response.status_code == 206 and response.content == b""
    assert response.headers["content-length"] == "1024" and response.headers["content-range"].startswith("bytes 0-1023/")


@pytest.mark.parametrize("path", ["/assets/missing.mp4", "/assets/Guardoath_ai_final.mp4"])
def test_unknown_videos_are_not_found(path):
    assert TestClient(create_app(credentials={"test-key": "demo"})).get(path, headers={"Range": "bytes=0-1"}).status_code == 404


def test_a_video_that_fits_in_one_part_is_sent_whole(tmp_path):
    from guardoath.app import media_part
    small = tmp_path / "small.mp4"
    small.write_bytes(b"0123456789")
    response = media_part(small, None, head=False)
    assert response.status_code == 200 and response.body == b"0123456789"
    assert response.headers["content-length"] == "10" and response.headers["accept-ranges"] == "bytes"
    assert media_part(small, None, head=True).body == b""
    assert media_part(small, "bytes=8-", head=False).body == b"89"


def test_the_page_offers_no_download_link_that_could_not_be_served():
    page = TestClient(create_app(credentials={"test-key": "demo"})).get("/dashboard").text
    video = page[page.index('<video id="demo-video"'):]
    assert "<a " not in video[:video.index("</video>")]


# The developer guide (guardoath/static/guide.html, served at /guide)

def guide_text():
    from pathlib import Path
    return (Path(__file__).resolve().parents[1] / "guardoath" / "static" / "guide.html").read_text(encoding="utf-8")


def dashboard_help():
    import json
    import re
    from pathlib import Path
    script = (Path(__file__).resolve().parents[1] / "guardoath" / "static" / "dashboard.js").read_text(encoding="utf-8")
    return (json.loads(re.search(r"^const checkHelp = (.*);$", script, re.M).group(1)),
            json.loads(re.search(r"^const profileHelp = (.*);$", script, re.M).group(1)))


def test_the_guide_is_served_as_a_page_without_scripts():
    response = TestClient(create_app(credentials={"test-key": "demo"})).get("/guide")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert "script-src 'none'" in response.headers["content-security-policy"]
    assert "<script" not in response.text and "Developer guide" in response.text
    assert TestClient(create_app(credentials={"test-key": "demo"})).get("/assets/guide.css").status_code == 200


def test_the_site_links_to_the_guide_and_never_to_the_source_repository():
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "guardoath" / "static"
    for path in [*static.glob("*.html"), *static.glob("*.js"), *static.glob("*.css")]:
        assert "github.com" not in path.read_text(encoding="utf-8").lower(), path.name
    page = TestClient(create_app(credentials={"test-key": "demo"})).get("/dashboard").text
    assert '<a href="/guide" target="_blank" rel="noopener noreferrer">Developer guide' in page


def test_the_guide_has_every_check_and_profile_in_the_dashboards_words():
    import html
    guide = guide_text()
    checks, profiles = dashboard_help()
    for check_id, help in checks.items():
        section = guide[guide.index(f'id="check-{check_id}"'):]
        section = section[:section.index("</section>")]
        for text in (help["what"], help["why"], help["limit"], "check_" + check_id):
            assert html.escape(text) in section, (check_id, text)
    for version, help in profiles.items():
        row = guide[guide.index(f'id="profile-{version}"'):]
        row = row[:row.index("</tr>")]
        assert html.escape(help["name"]) in row and html.escape(help["what"]) in row, version


def test_the_guide_shows_the_real_checks_and_mode_of_each_example_profile():
    import json
    from pathlib import Path
    guide = guide_text()
    policies = json.loads((Path(__file__).resolve().parents[1] / "policies" / "playground.json").read_text(encoding="utf-8"))["policies"]
    for policy in policies:
        row = guide[guide.index(f'id="profile-{policy["version"]}"'):]
        cells = row[:row.index("</tr>")].split("<td>")
        assert cells[3].startswith(", ".join(rule["checker_id"] for rule in policy["rules"])), policy["version"]
        assert cells[4].startswith(policy.get("mode", "observe")), policy["version"]
    # Every example profile observes, so the guide must say so.
    assert {policy.get("mode", "observe") for policy in policies} == {"observe"}
    assert "The example profiles run in observe mode" in guide


def test_every_link_inside_the_guide_goes_somewhere():
    import re
    guide = guide_text()
    ids = set(re.findall(r'id="([^"]+)"', guide))
    for target in re.findall(r'href="#([^"]+)"', guide):
        assert target in ids, target
    for href in re.findall(r'href="([^"#]+)"', guide):
        assert href in {"/assets/dashboard.css", "/assets/guide.css", "/dashboard", "/docs"}, href


def test_the_guide_documents_every_public_endpoint_and_request_field():
    from guardoath.app import KEY_HEADER
    from guardoath.models import ApiCheckRequest
    guide = guide_text()
    app = create_app(credentials={"test-key": "demo"})
    # Validation packs are not offered on tenant records, so the guide leaves them out on purpose.
    left_out = {"/v1/validation-packs"}
    paths = {route.path for route in app.routes if getattr(route, "path", "").startswith("/v1/")} - left_out
    assert paths and all(f"<code>{path}</code>" in guide for path in paths), paths
    for field in set(ApiCheckRequest.model_fields) - {"validation_codes"}:
        assert f"<td><code>{field}</code></td>" in guide, field
    assert KEY_HEADER in guide and "x-amz-content-sha256" in guide and "<code>/healthz</code>" in guide


def test_the_guide_examples_hash_the_exact_body_they_send():
    import html
    guide = html.unescape(guide_text())
    assert "hashlib.sha256(body).hexdigest()" in guide and "data=body" in guide
    assert 'createHash("sha256").update(body, "utf8").digest("hex")' in guide and "    body,\n" in guide


def test_the_guides_python_example_works_against_the_api(monkeypatch):
    # Run the guide's own Python example against the app, through its decision helper.
    import html
    import io
    import json
    import re
    import urllib.request
    from guardoath.checkers import default_checkers
    from guardoath.hosted import TenantService
    from guardoath.tenant_record import demo_key, demo_records
    from guardoath.tenant_store import MemoryTenantStore
    import pytest
    pytest.importorskip("tldextract")
    secret = "guide-example-secret-" + "g" * 32
    from guardoath.config import load_policies
    tenants = TenantService(MemoryTenantStore(demo_records(key_secret=secret)), default_checkers(enable_pii=True), secret,
                            tuple(load_policies("policies/playground.json")))
    client = TestClient(create_app(credentials={}, tenants=tenants))
    guide = html.unescape(guide_text())
    blocks = re.findall(r"<pre><code>(.*?)</code></pre>", guide, re.S)
    example = next(b for b in blocks if b.startswith("import hashlib"))
    decide = next(b for b in blocks if b.startswith("def next_step"))
    sent = []

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None):
        headers = {k.lower(): v for k, v in request.header_items()}
        sent.append(headers)
        response = client.post(request.full_url.replace("https://guardoath.com", ""), content=request.data, headers=headers)
        assert response.status_code == 200, response.text
        return Reply(response.content)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    namespace = {}
    exec(compile(example.replace('"gok_demo1_..."', repr(demo_key("demo1", secret))), "guide-example", "exec"), namespace)
    exec(compile(decide, "guide-decide", "exec"), namespace)
    import hashlib
    assert sent[0]["x-amz-content-sha256"] == hashlib.sha256(json.dumps({"application_id": "demo1", "content": "Please email jane@example.com about my order."}).encode()).hexdigest()
    blocked = namespace["check"]("input", "Please email jane@example.com about my order.")
    assert blocked["effective_action"] == "block" and namespace["next_step"](blocked) == "stop"
    clean = namespace["check"]("output", "Can you explain your return policy?")
    assert namespace["next_step"](clean) == "continue"
    observed = namespace["check"]("input", "Please email jane@example.com about my order.", profile="pii-v1")
    assert observed["recommended_action"] == "flag" and observed["effective_action"] == "allow" and namespace["next_step"](observed) == "continue"
