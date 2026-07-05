"""Typed application errors that render as RFC-7807 problem+json.

Handlers raise these instead of ``HTTPException`` so that the mapping from a
domain failure (quota exceeded, not found, ...) to an HTTP status lives in one
place and the error body is machine-readable.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, detail: str, *, code: str | None = None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class BadRequest(AppError):
    status_code = 400
    code = "bad_request"


class Conflict(AppError):
    """Used for quota breaches and state conflicts (e.g. patch while patching)."""

    status_code = 409
    code = "conflict"


class ProvisioningError(AppError):
    status_code = 502
    code = "provisioning_failed"


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"type": f"about:blank#{exc.code}", "title": exc.code, "detail": exc.detail},
    )
