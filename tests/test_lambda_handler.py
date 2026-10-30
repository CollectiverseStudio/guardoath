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
