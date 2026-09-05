"""API hardening middleware: request size limits, rate limiting, and metrics."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_EXEMPT_PATHS = frozenset({"/health", "/health/ready", "/metrics", "/docs", "/openapi.json", "/redoc"})


class MaxBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in {"POST", "PUT", "PATCH"} and request.url.path not in _EXEMPT_PATHS:
            content_length = request.headers.get("content-length")
            if content_length is not None and int(content_length) > self.max_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"type": "about:blank", "title": "Payload Too Large", "status": 413},
                )
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple sliding-window rate limiter keyed by client IP."""

    def __init__(self, app, max_requests: int, window_seconds: int):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
        return request.client.host if request.client else "unknown"

    def _allow(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.max_requests:
                retry_after = max(1, int(self.window_seconds - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
            return True, 0

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)

        allowed, retry_after = self._allow(self._client_ip(request))
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"type": "about:blank", "title": "Too Many Requests", "status": 429},
                headers={"Retry-After": str(retry_after)},
            )
        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Increment ``mdbaas_http_requests_total`` using route templates (low cardinality)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", None) or "unmatched"
        from app.observability import observe_http_request

        observe_http_request(request.method, path, response.status_code)
        return response
