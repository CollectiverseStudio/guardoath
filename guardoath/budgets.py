"""Immediate admission limits for one server process; no queues or persistence."""

from contextlib import contextmanager
from dataclasses import dataclass
import math
import os
import re
from threading import Lock
from time import monotonic

from fastapi import HTTPException
from pydantic import Field
from starlette.responses import JSONResponse

from guardoath.models import Contract


class ResourceLimits(Contract):
    checks_per_minute: int = Field(default=120, ge=1, le=100_000, strict=True)
    burst: int = Field(default=20, ge=1, le=10_000, strict=True)
    application_concurrency: int = Field(default=4, ge=1, le=1024, strict=True)
    process_concurrency: int = Field(default=16, ge=1, le=1024, strict=True)
    ingress_concurrency: int = Field(default=32, ge=1, le=1024, strict=True)

    @classmethod
    def from_environment(cls):
        values = {}
        for name in cls.model_fields:
            variable = 'GUARDOATH_' + name.upper()
            raw = os.getenv(variable)
            if raw is not None:
                if len(raw) > 6 or not re.fullmatch(r'[0-9]+', raw, re.ASCII):
                    raise ValueError(f'{variable} must be a bounded positive integer')
                values[name] = int(raw)
        return cls(**values)


@dataclass
class ApplicationUsage:
    tokens: float
    updated_at: float
    active: int = 0


def reject(status, detail, retry_after=1):
    raise HTTPException(status, detail, headers={'Retry-After': str(retry_after), 'Cache-Control': 'no-store'})


class RequestBudgets:
    def __init__(self, limits, application_ids, clock=monotonic):
        self.limits = limits
        self._clock = clock
        self._lock = Lock()
        now = clock()
        # Only trusted credential bindings create state; rotating keys share it.
        self._applications = {key: ApplicationUsage(float(limits.burst), now) for key in application_ids}
        self._checks = self._ingress = 0

    def register(self, application_id):
        """Start tracking an application authenticated by its tenant record."""
        with self._lock:
            self._applications.setdefault(application_id, ApplicationUsage(float(self.limits.burst), self._clock()))

    @contextmanager
    def ingress(self):
        with self._lock:
            if self._ingress >= self.limits.ingress_concurrency:
                reject(503, 'Request capacity is temporarily exhausted')
            self._ingress += 1
        try:
            yield
        finally:
            with self._lock:
                self._ingress -= 1

    @contextmanager
    def check(self, application_id):
        with self._lock:
            usage = self._applications[application_id]
            if usage.active >= self.limits.application_concurrency:
                reject(429, 'Application check concurrency limit reached')
            if self._checks >= self.limits.process_concurrency:
                reject(503, 'Check capacity is temporarily exhausted')
            now = self._clock()
            rate = self.limits.checks_per_minute / 60
            usage.tokens = min(float(self.limits.burst), usage.tokens + max(0, now - usage.updated_at) * rate)
            usage.updated_at = now
            if usage.tokens < 1:
                reject(429, 'Application check rate limit reached', max(1, math.ceil((1 - usage.tokens) / rate)))
            usage.tokens -= 1
            usage.active += 1
            self._checks += 1
        try:
            yield
        finally:
            with self._lock:
                usage.active -= 1
                self._checks -= 1


class RequestCapacity:
    """Outer POST /v1/ gate, before JSON bodies are read or parsed."""

    def __init__(self, app, budgets):
        self.app, self.budgets = app, budgets

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] != 'POST' or not scope['path'].startswith('/v1/'):
            return await self.app(scope, receive, send)
        # Catch admission failures only; downstream exceptions propagate normally.
        slot = self.budgets.ingress()
        try:
            slot.__enter__()
        except HTTPException as exc:
            return await JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                      headers=exc.headers)(scope, receive, send)
        try:
            return await self.app(scope, receive, send)
        finally:
            slot.__exit__(None, None, None)
