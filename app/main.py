"""Application entrypoint: ``uvicorn app.main:app``.

The control plane keeps serving reads/writes of *desired* state even if the
scheduler or Docker is unavailable — provisioning simply queues until the workers
run. Startup therefore tolerates a missing Docker daemon unless the scheduler is
enabled.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api import api_router
from app.bootstrap import ensure_seed
from app.config import get_settings
from app.db import init_db, session_scope
from app.errors import AppError, app_error_handler

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    init_db()

    if settings.enable_bootstrap:
        with session_scope() as session:
            api_key = ensure_seed(session)
        if api_key:
            # Shown once — the only time a usable admin key is printed.
            log.info("bootstrap admin API key (store it now): %s", api_key)

    scheduler = None
    if settings.enable_scheduler:
        from app.lifecycle.scheduler import LifecycleScheduler
        from app.runtime import get_provisioner

        scheduler = LifecycleScheduler(get_provisioner())
        scheduler.start()

    app.state.scheduler = scheduler
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Mini-DBaaS", version=__version__, lifespan=lifespan)
    app.add_exception_handler(AppError, app_error_handler)
    app.include_router(api_router, prefix=settings.api_prefix)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
