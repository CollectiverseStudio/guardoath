"""Local development API. Credentials bind requests to one application."""

import hmac
import os
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from valinator.checkers import default_checkers
from valinator.engine import Engine
from valinator.models import Action, CheckRequest, CheckResponse, Policy, Rule, Stage


def demo_engine() -> Engine:
    return Engine([Policy(
        application_id="demo", version="support-v1", timeout_ms=100,
        rules=(Rule(checker_id="schema", on_finding=Action.BLOCK),
               Rule(checker_id="safety", on_finding=Action.BLOCK),
               Rule(checker_id="bias", on_finding=Action.FLAG)),
        input_schema={"type": "object", "properties": {"message": {"type": "string"}},
                      "required": ["message"], "additionalProperties": False},
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}},
                       "required": ["answer"], "additionalProperties": False},
    )], default_checkers())


def create_app(engine: Engine | None = None, credentials: dict[str, str] | None = None) -> FastAPI:
    service = engine or demo_engine()
    # No embedded credential. Without configuration, checks fail closed with 503.
    key = os.getenv("VALINATOR_API_KEY", "")
    bindings = dict(credentials) if credentials is not None else ({key: "demo"} if key else {})
    if any(not token or not app_id for token, app_id in bindings.items()):
        raise ValueError("Credentials and application identifiers must be nonempty")
    api = FastAPI(title="Valinator", version="0.1.0")
    bearer = HTTPBearer(auto_error=False)

    @api.exception_handler(RequestValidationError)
    async def validation_error(_request, _exc):
        # FastAPI's default 422 echoes invalid submitted values.
        return JSONResponse(status_code=422, content={"detail": "Invalid check request"})

    async def authenticate(
        auth: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> str:
        if not bindings:
            raise HTTPException(503, "Application credentials are not configured")
        if auth is not None:
            for token, app_id in bindings.items():
                if hmac.compare_digest(auth.credentials.encode(), token.encode()):
                    return app_id
        raise HTTPException(401, "Invalid application credential", headers={"WWW-Authenticate": "Bearer"})

    async def assess(request: CheckRequest, application_id: str, stage: Stage):
        if request.application_id != application_id:
            raise HTTPException(403, "Credential does not authorize this application")
        if not service.has_policy(application_id, request.policy_version):
            raise HTTPException(404, "Policy version not found")
        return await service.check(request, stage)

    @api.get("/healthz")
    async def health():
        return {"status": "ok", "profile": "local-prototype"}

    @api.post("/v1/checks/input", response_model=CheckResponse)
    async def check_input(request: CheckRequest, app_id: Annotated[str, Depends(authenticate)]):
        return await assess(request, app_id, Stage.INPUT)

    @api.post("/v1/checks/output", response_model=CheckResponse)
    async def check_output(request: CheckRequest, app_id: Annotated[str, Depends(authenticate)]):
        return await assess(request, app_id, Stage.OUTPUT)

    return api


app = create_app()
