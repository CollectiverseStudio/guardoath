"""Bound JSON request bodies before FastAPI buffers or parses them."""

import asyncio

from starlette.responses import JSONResponse

from valinator.json_data import MAX_JSON_BYTES, parse_json


class JsonIngressLimits:
    def __init__(self, app, max_bytes=MAX_JSON_BYTES, read_timeout=5.0):
        self.app, self.max_bytes, self.read_timeout = app, max_bytes, read_timeout

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not scope["path"].startswith("/v1/"):
            return await self.app(scope, receive, send)

        async def reject(status, detail):
            await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)

        headers = {}
        for key, value in scope["headers"]:
            key = key.lower()
            if key in headers and key in {b"content-type", b"content-length", b"content-encoding"}:
                return await reject(400, "Ambiguous request headers")
            headers[key] = value
        media = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if media != b"application/json" and not (media.startswith(b"application/") and media.endswith(b"+json")):
            return await reject(415, "A JSON content type is required")
        for parameter in headers.get(b"content-type", b"").split(b";")[1:]:
            name, _, value = parameter.strip().partition(b"=")
            if name.lower() == b"charset" and value.strip(b'"').lower() not in {b"utf-8", b"utf8"}:
                return await reject(415, "JSON requests must use UTF-8")
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            return await reject(415, "Compressed request bodies are not supported")
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            return await reject(400, "Invalid request length")
        if declared < 0:
            return await reject(400, "Invalid request length")
        if declared > self.max_bytes:
            return await reject(413, "Request body exceeds the byte limit")
        body = bytearray()
        try:
            async with asyncio.timeout(self.read_timeout):
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        return
                    chunk = event.get("body", b"")
                    if len(body) + len(chunk) > self.max_bytes:
                        return await reject(413, "Request body exceeds the byte limit")
                    body.extend(chunk)
                    if not event.get("more_body", False):
                        break
        except TimeoutError:
            return await reject(408, "Request body read deadline exceeded")
        try:
            parse_json(body.decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError):
            return await reject(400, "Invalid JSON request or parser limit exceeded")
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()
        return await self.app(scope, replay, send)
