"""The Lambda entry point (guardoath.lambda_handler): function URL events in, function URL responses out."""
import base64
import json

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response

from guardoath.lambda_handler import MAX_RESPONSE_BODY, lambda_response, make_handler


def event(method="GET", path="/", query="", headers=None, body=None, binary=False, cookies=None):
    raw = None if body is None else (base64.b64encode(body).decode() if binary else body.decode())
    return {"version": "2.0", "rawPath": path, "rawQueryString": query, "headers": headers or {},
            **({"cookies": cookies} if cookies else {}),
            "requestContext": {"http": {"method": method, "path": path, "sourceIp": "198.51.100.7"},
                               "domainName": "abc.lambda-url.us-east-1.on.aws"},
            **({"body": raw, "isBase64Encoded": binary} if raw is not None else {"isBase64Encoded": False})}


@pytest.fixture
def echo():
    app = FastAPI()

    @app.api_route("/echo/{name}", methods=["GET", "POST", "HEAD"])
    async def echo_route(name: str, request: Request):
        body = await request.body()
        data = {"name": name, "method": request.method, "query": dict(request.query_params),
                "header": request.headers.get("x-test"), "cookie": request.cookies, "client": request.client.host,
                "body": body.decode("latin-1"), "path": request.url.path}
        response = Response(json.dumps(data), media_type="application/json")
        response.set_cookie("a", "1")
        response.set_cookie("b", "2")
        return response

    @app.get("/binary")
    async def binary():
        return Response(bytes(range(256)), media_type="application/octet-stream")

    @app.get("/huge")
    async def huge():
        return Response("x" * (MAX_RESPONSE_BODY + 1), media_type="text/plain")

    return app


def test_a_request_reaches_the_app_with_everything_it_carries(echo):
    handler = make_handler(lambda: echo)
    out = handler(event("POST", "/echo/a%20b", "x=1&y=two", {"X-Test": "yes", "content-type": "text/plain"},
                        b"payload", cookies=["s=1", "t=2"]))
    assert out["statusCode"] == 200 and out["isBase64Encoded"] is False
    data = json.loads(out["body"])
    assert data == {"name": "a b", "method": "POST", "query": {"x": "1", "y": "two"}, "header": "yes",
                    "cookie": {"s": "1", "t": "2"}, "client": "198.51.100.7", "body": "payload", "path": "/echo/a b"}
    assert sorted(c.split(";")[0] for c in out["cookies"]) == ["a=1", "b=2"]
    assert "set-cookie" not in out["headers"]


def test_a_base64_request_body_is_decoded(echo):
    out = make_handler(lambda: echo)(event("POST", "/echo/x", body=bytes([0, 255, 10]), binary=True))
    assert json.loads(out["body"])["body"] == bytes([0, 255, 10]).decode("latin-1")


def test_a_binary_response_is_base64_encoded(echo):
    out = make_handler(lambda: echo)(event("GET", "/binary"))
    assert out["isBase64Encoded"] is True and base64.b64decode(out["body"]) == bytes(range(256))


def test_a_response_too_large_for_lambda_is_refused_with_a_reason(echo):
    out = make_handler(lambda: echo)(event("GET", "/huge"))
    assert out["statusCode"] == 500 and out["body"] == "The response was too large for this server."


def test_the_app_is_created_once(echo):
    calls = []

    def factory():
        calls.append(1)
        return echo

    handler = make_handler(factory)
    for _ in range(3):
        assert handler(event("GET", "/echo/z"))["statusCode"] == 200
    assert calls == [1]


def test_repeated_headers_are_joined():
    out = lambda_response(200, [(b"vary", b"a"), (b"vary", b"b"), (b"content-type", b"text/plain")], b"ok")
    assert out["headers"]["vary"] == "a, b" and out["body"] == "ok"


def test_guardoath_runs_through_the_handler():
    from guardoath.app import create_app
    handler = make_handler(lambda: create_app(credentials={"local-key": "demo"}))
    health = handler(event("GET", "/healthz"))
    assert health["statusCode"] == 200 and json.loads(health["body"])["status"] == "ok"
    body = json.dumps({"application_id": "demo", "policy_version": "support-v1", "content": json.dumps({"message": "hi"}),
                       "content_format": "json"}).encode()
    checked = handler(event("POST", "/v1/checks/input", headers={"x-guardoath-key": "local-key", "content-type": "application/json"},
                            body=body))
    assert checked["statusCode"] == 200 and json.loads(checked["body"])["application_id"] == "demo"
    assert handler(event("GET", "/v1/applications/current"))["statusCode"] == 401


def test_every_video_part_fits_in_a_lambda_response():
    from pathlib import Path
    from guardoath.app import create_app
    handler = make_handler(lambda: create_app(credentials={"local-key": "demo"}))
    data = (Path(__file__).resolve().parents[1] / "guardoath" / "static" / "guardoath_ai_final.mp4").read_bytes()
    received, start = b"", 0
    while start < len(data):
        out = handler(event("GET", "/assets/guardoath_ai_final.mp4", headers={"range": f"bytes={start}-"}))
        assert out["statusCode"] == 206 and out["isBase64Encoded"] is True and len(out["body"]) <= MAX_RESPONSE_BODY
        part = base64.b64decode(out["body"])
        received += part
        start += len(part)
    assert received == data


# The request log line (one per request; never content, query strings, keys or other headers)

def test_each_request_logs_one_line_without_content_or_secrets(echo, capsys):
    import json as json_module
    handler = make_handler(lambda: echo)
    secret_body = b'{"content": "jane@example.com"}'
    handler(event("POST", "/echo/a", "token=SECRETQUERY", {"x-guardoath-key": "gok_demo1_" + "k" * 43,
                  "authorization": "Bearer SECRETAUTH", "content-type": "application/json"}, secret_body))
    handler(event("GET", "/echo/b", headers={"range": "bytes=0-1023", "user-agent": "Amazon CloudFront"}))
    handler(event("GET", "/echo/c", headers={"range": "bytes=0-1, 5-9"}))
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 3
    first, second, third = (json_module.loads(line) for line in lines)
    assert first["request"] == "POST /echo/a" and first["status"] == 200 and first["first_in_copy"] is True
    assert first["cloudfront"] is False and "range" not in first and first["ms"] >= 0
    assert second == {**second, "request": "GET /echo/b", "range": "bytes=0-1023", "cloudfront": True, "first_in_copy": False}
    assert third["range"] == "other"
    text = "\n".join(lines)
    for private in ("SECRETQUERY", "SECRETAUTH", "gok_demo1_", "jane@example.com", "token="):
        assert private not in text, private


def test_the_logged_status_is_the_status_lambda_returns(echo, capsys):
    import json as json_module
    out = make_handler(lambda: echo)(event("GET", "/huge"))
    logged = json_module.loads(capsys.readouterr().out.strip())
    assert out["statusCode"] == 500 and logged["status"] == 500 and logged["request"] == "GET /huge"


# Building the app while Lambda initializes the copy (index.py calls handler.prepare())

def test_prepare_builds_the_app_once_before_the_first_request(echo, capsys):
    calls = []

    def factory():
        calls.append(1)
        return echo

    handler = make_handler(factory)
    handler.prepare()
    prepared = json.loads(capsys.readouterr().out.strip())
    assert calls == [1] and prepared["prepared"] == "app" and prepared["ms"] >= 0 and set(prepared) == {"prepared", "ms"}
    assert handler(event("GET", "/echo/a"))["statusCode"] == 200
    assert handler(event("GET", "/echo/b"))["statusCode"] == 200
    first, second = (json.loads(line) for line in capsys.readouterr().out.strip().splitlines())
    assert calls == [1]
    assert first["first_in_copy"] is True and "built_app" not in first
    assert second["first_in_copy"] is False and "built_app" not in second
    handler.prepare()
    assert calls == [1] and capsys.readouterr().out == ""


def test_without_prepare_the_first_request_builds_the_app_and_its_log_line_says_so(echo, capsys):
    handler = make_handler(lambda: echo)
    handler(event("GET", "/echo/a"))
    handler(event("GET", "/echo/b"))
    first, second = (json.loads(line) for line in capsys.readouterr().out.strip().splitlines())
    assert first["built_app"] is True and first["first_in_copy"] is True
    assert "built_app" not in second and second["first_in_copy"] is False


def test_a_failing_build_is_not_hidden_and_is_tried_again(echo):
    calls = []

    def factory():
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("bad configuration")
        return echo

    handler = make_handler(factory)
    with pytest.raises(ValueError, match="bad configuration"):
        handler.prepare()
    assert handler(event("GET", "/echo/a"))["statusCode"] == 200 and calls == [1, 1]
