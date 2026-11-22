"""Same-origin enrollment endpoints with bounded, non-reflecting request parsing."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from guardoath.admin_enrollment import EnrollmentCodeMismatch, EnrollmentUnavailable

_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}


def _response(value: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers=_HEADERS)


async def _body(request: Request, fields: set[str]) -> dict | JSONResponse:
    # Inspect the origin before parsing any credentials. Never derive the
    # accepted origin from Host, forwarded headers or the configured callback.
    if request.headers.getlist("origin") != ["https://guardoath.com"]:
        return _response({"detail": "Authenticator setup denied"}, 403)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        return _response({"detail": "Invalid authenticator setup request"}, 400)
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 24_000:
                return _response({"detail": "Invalid authenticator setup request"}, 413)
            raw.extend(chunk)
        def unique(pairs):
            value = {}
            for name, item in pairs:
                if name in value:
                    raise ValueError()
                value[name] = item
            return value
        value = json.loads(raw, object_pairs_hook=unique)
    except Exception:
        return _response({"detail": "Invalid authenticator setup request"}, 400)
    # Avoid FastAPI's normal validation-error input reflection for passwords,
    # setup tickets and OTPs. Unexpected fields and types have the same error.
    if (not isinstance(value, dict) or set(value) != fields
            or not all(isinstance(item, str) for item in value.values())):
        return _response({"detail": "Invalid authenticator setup request"}, 400)
    return value


def create_enrollment_router(enrollment) -> APIRouter:
    router = APIRouter()

    async def invoke(request, fields, method):
        body = await _body(request, fields)
        if isinstance(body, JSONResponse):
            return body
        try:
            result = await run_in_threadpool(method, **body)
        except EnrollmentCodeMismatch:
            return _response({"detail": "Authenticator code was not accepted. Use a current code or cancel setup.",
                              "code": "code_mismatch"}, 400)
        except EnrollmentUnavailable:
            return _response({"detail": "Authenticator setup could not be confirmed. Try administrator sign-in; "
                                          "if setup is incomplete, contact the operator."}, 503)
        except Exception:
            return _response({"detail": "Authenticator setup unavailable"}, 503)
        if not result:
            return _response({"detail": "First authenticator setup was not authorized. "
                                          "Use administrator sign-in if already enrolled."}, 401)
        return result

    @router.post("/v1/admin/auth/enrollment/start")
    async def start(request: Request):
        result = await invoke(request, {"email", "password"}, enrollment.begin)
        return result if isinstance(result, JSONResponse) else _response(result)

    @router.post("/v1/admin/auth/enrollment/finish")
    async def finish(request: Request):
        result = await invoke(request, {"ticket", "code"}, enrollment.finish)
        return result if isinstance(result, JSONResponse) else _response({"status": "completed", "sign_in_required": True})

    @router.post("/v1/admin/auth/enrollment/cancel")
    async def cancel(request: Request):
        result = await invoke(request, {"ticket"}, enrollment.cancel)
        return result if isinstance(result, JSONResponse) else _response({"status": "cancelled"})

    return router
