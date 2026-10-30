"""AWS Lambda entry point: runs the GuardOath ASGI app for Lambda function URL requests.

Lambda handler setting: guardoath.lambda_handler.handler. The function URL sends payload format 2.0
events and expects the matching response (statusCode, headers, cookies, body, isBase64Encoded). The
response is buffered, so it must fit Lambda's 6 MB response limit; the app sends videos in parts
for that reason (see MEDIA_PART_BYTES in guardoath.app).

The app is created on the first request, from the environment, and reused while the Lambda execution
environment lives. One event loop is kept for the same lifetime.
"""
import asyncio
import base64
from urllib.parse import unquote

# A function URL response, base64 included, must stay under 6 MB; leave room for headers and JSON.
MAX_RESPONSE_BODY = 5_800_000
TEXT_TYPES = ("text/", "application/json", "application/javascript", "application/xml", "image/svg+xml")


def scope_for(event: dict) -> tuple[dict, bytes]:
    http = event["requestContext"]["http"]
    headers = [(name.lower().encode("latin-1"), str(value).encode("latin-1"))
               for name, value in (event.get("headers") or {}).items()]
    if event.get("cookies"):
        headers.append((b"cookie", "; ".join(event["cookies"]).encode("latin-1")))
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf-8")
    raw_path = event.get("rawPath") or http.get("path") or "/"
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
        "method": http["method"].upper(), "scheme": "https",
        "path": unquote(raw_path), "raw_path": raw_path.encode("latin-1"),
        "query_string": (event.get("rawQueryString") or "").encode("latin-1"),
        "root_path": "", "headers": headers,
        "client": (http.get("sourceIp") or "0.0.0.0", 0),
        "server": ((event.get("requestContext") or {}).get("domainName") or "lambda", 443),
    }
    return scope, raw


async def run_app(app, scope: dict, body: bytes) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    finished = asyncio.Event()
    delivered = False
    status = 500
    headers: list[tuple[bytes, bytes]] = []
    chunks: list[bytes] = []

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}
        # Nothing more arrives; report a disconnect only once the response is complete.
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status, headers
        if message["type"] == "http.response.start":
            status = message["status"]
            headers = list(message.get("headers") or [])
        elif message["type"] == "http.response.body":
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                finished.set()

    try:
        await app(scope, receive, send)
    finally:
        finished.set()
    return status, headers, b"".join(chunks)


def lambda_response(status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> dict:
    out_headers: dict[str, str] = {}
    cookies: list[str] = []
    content_type = ""
    for name, value in headers:
        key, text = name.decode("latin-1").lower(), value.decode("latin-1")
        if key == "set-cookie":
            cookies.append(text)
            continue
        if key == "content-type":
            content_type = text.lower()
        out_headers[key] = out_headers[key] + ", " + text if key in out_headers else text
    textual = content_type.startswith(TEXT_TYPES)
    if textual:
        try:
            payload, encoded = body.decode("utf-8"), False
        except UnicodeDecodeError:
            payload, encoded = base64.b64encode(body).decode("ascii"), True
    else:
        payload, encoded = base64.b64encode(body).decode("ascii"), True
    if len(payload) > MAX_RESPONSE_BODY:
        # Never hand Lambda a response it cannot return; say what happened instead.
        return {"statusCode": 500, "headers": {"content-type": "text/plain; charset=utf-8", "cache-control": "no-store"},
                "body": "The response was too large for this server.", "isBase64Encoded": False}
    response = {"statusCode": status, "headers": out_headers, "body": payload, "isBase64Encoded": encoded}
    if cookies:
        response["cookies"] = cookies
    return response


def make_handler(app_factory):
    """A Lambda handler for the ASGI app returned by app_factory (called once, on the first request)."""
    state: dict = {}

    def handler(event, context=None):
        if "app" not in state:
            state["loop"] = asyncio.new_event_loop()
            state["app"] = app_factory()
        scope, body = scope_for(event)
        status, headers, payload = state["loop"].run_until_complete(run_app(state["app"], scope, body))
        return lambda_response(status, headers, payload)

    return handler


def _guardoath_app():
    # guardoath.app builds its app from the environment when it is imported.
    from guardoath.app import app
    return app


handler = make_handler(_guardoath_app)
