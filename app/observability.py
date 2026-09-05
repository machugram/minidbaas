"""Health checks and Prometheus metrics (design-review §5.2)."""

from __future__ import annotations

import logging
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Info, generate_latest
from sqlalchemy import func, select, text
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from app import __version__
from app.config import Settings, get_settings
from app.db import engine, session_scope
from app.models import Instance, Job, JobState

log = logging.getLogger(__name__)

INFO = Info("mdbaas", "Mini-DBaaS build info")
INFO.info({"version": __version__})

JOBS_QUEUED = Gauge("mdbaas_jobs_queued", "Jobs waiting in the queue")
JOBS_RUNNING = Gauge("mdbaas_jobs_running", "Jobs currently claimed by a worker")
JOBS_FAILED = Gauge("mdbaas_jobs_failed", "Jobs in the dead-letter (failed) state")
JOBS_OLDEST_AGE = Gauge(
    "mdbaas_jobs_oldest_queued_age_seconds",
    "Age in seconds of the oldest queued job (0 if none)",
)
INSTANCES = Gauge("mdbaas_instances", "Managed instances by observed state", ["state"])
HTTP_REQUESTS = Counter(
    "mdbaas_http_requests_total",
    "HTTP requests handled",
    ["method", "path", "status"],
)


def observe_http_request(method: str, path: str, status: int) -> None:
    """Record one completed HTTP request. Path should be a low-cardinality template."""
    HTTP_REQUESTS.labels(method=method, path=path, status=str(status)).inc()



def _check_database() -> dict[str, Any]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        log.warning("database health check failed: %s", exc)
        return {"status": "error", "detail": str(exc)[:200]}


def _check_docker(settings: Settings) -> dict[str, Any]:
    if settings.enable_scheduler is False:
        return {"status": "skipped", "detail": "scheduler disabled"}
    try:
        from app.runtime import get_provisioner

        provisioner = get_provisioner()
        ping = getattr(provisioner, "ping", None)
        if ping is None or not ping():
            return {"status": "error", "detail": "docker daemon unreachable"}
        return {"status": "ok"}
    except Exception as exc:  # noqa: BLE001
        log.warning("docker health check failed: %s", exc)
        return {"status": "error", "detail": str(exc)[:200]}


def _check_scheduler(request: Request, settings: Settings) -> dict[str, Any]:
    if not settings.enable_scheduler:
        return {"status": "skipped", "enabled": False}
    scheduler = getattr(request.app.state, "scheduler", None)
    if scheduler is None:
        return {"status": "error", "enabled": True, "detail": "scheduler not started"}
    running = getattr(scheduler, "running", False)
    if not running:
        return {"status": "error", "enabled": True, "detail": "scheduler not running"}
    return {"status": "ok", "enabled": True}


def readiness(request: Request) -> tuple[dict[str, Any], int]:
    settings = get_settings()
    checks = {
        "database": _check_database(),
        "docker": _check_docker(settings),
        "scheduler": _check_scheduler(request, settings),
    }
    critical = ("database",)
    if settings.enable_scheduler:
        critical = ("database", "docker", "scheduler")

    failed = [name for name in critical if checks[name]["status"] == "error"]
    overall = "ok" if not failed else "degraded"
    status_code = 200 if not failed else 503
    return (
        {"status": overall, "version": __version__, "checks": checks},
        status_code,
    )


def refresh_metrics() -> None:
    from app.clock import utcnow

    with session_scope() as session:
        JOBS_QUEUED.set(
            session.scalar(
                select(func.count()).select_from(Job).where(Job.state == JobState.QUEUED.value)
            )
            or 0
        )
        JOBS_RUNNING.set(
            session.scalar(
                select(func.count()).select_from(Job).where(Job.state == JobState.RUNNING.value)
            )
            or 0
        )
        JOBS_FAILED.set(
            session.scalar(
                select(func.count()).select_from(Job).where(Job.state == JobState.FAILED.value)
            )
            or 0
        )

        oldest = session.scalar(
            select(func.min(Job.created_at)).where(Job.state == JobState.QUEUED.value)
        )
        if oldest is None:
            JOBS_OLDEST_AGE.set(0)
        else:
            JOBS_OLDEST_AGE.set(max(0.0, (utcnow() - oldest).total_seconds()))

        for state, count in session.execute(
            select(Instance.observed_state, func.count()).group_by(Instance.observed_state)
        ):
            INSTANCES.labels(state=state).set(count)


def metrics_response() -> Response:
    refresh_metrics()
    return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
