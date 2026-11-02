"""Separate testable ASGI surface; does not modify/register the hosted application."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .service import InteractionRequest
from guardoath.tenant_record import TenantRecord


class EnvelopeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > 65_536:
                return await JSONResponse({'detail': 'Request envelope too large'}, status_code=413)(scope, receive, send)
            chunks.append(chunk)
            if not message.get('more_body', False):
                break
        body = b''.join(chunks)
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        return await self.app(scope, replay, send)


def create_prototype_app(gateway, authenticate):
    app = FastAPI(title='GuardOath isolated gateway prototype')
    app.add_middleware(EnvelopeLimit)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({'detail': 'Invalid interaction request'}, status_code=422)

    @app.post('/v1/interactions')
    async def interact(body: InteractionRequest, request: Request):
        if request.query_params:
            return JSONResponse({'detail': 'Query overrides are not supported'}, status_code=422)
        key = request.headers.get('x-guardoath-key')
        if key is None:
            authorization = request.headers.get('authorization', '')
            key = authorization[7:] if authorization.startswith('Bearer ') else ''
        try:
            record = authenticate(key)
        except Exception:
            record = None
        if not isinstance(record, TenantRecord) or record.status != 'active':
            return JSONResponse({'detail': 'Unauthorized'}, status_code=401)
        result = await gateway.interact(record, body)
        status = {'unauthorized': 401, 'idempotency_conflict': 409, 'quota_exceeded': 429,
                  'gateway_busy': 429, 'provider_unavailable': 403, 'configuration_error': 409,
                  'provider_error': 502, 'provider_timeout': 504, 'gateway_timeout': 504,
                  'validation_incomplete': 503, 'operation_cancelled': 409}.get(result.status, 200)
        return JSONResponse(result.model_dump(mode='json'), status_code=status,
                            headers={'X-GuardOath-Decision': result.status})
    return app
