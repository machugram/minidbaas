"""Rate limiting and request size middleware."""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import get_settings
from app.middleware import MaxBodySizeMiddleware, RateLimitMiddleware


@pytest.fixture()
def limited_client():
    os.environ["MDBAAS_RATE_LIMIT_REQUESTS"] = "2"
    os.environ["MDBAAS_RATE_LIMIT_WINDOW_SECONDS"] = "60"
    os.environ["MDBAAS_MAX_REQUEST_BYTES"] = "32"
    get_settings.cache_clear()

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, max_requests=2, window_seconds=60)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=32)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    @app.post("/echo")
    def echo(body: dict):
        return body

    with TestClient(app) as client:
        yield client

    os.environ.pop("MDBAAS_RATE_LIMIT_REQUESTS", None)
    os.environ.pop("MDBAAS_RATE_LIMIT_WINDOW_SECONDS", None)
    os.environ.pop("MDBAAS_MAX_REQUEST_BYTES", None)
    get_settings.cache_clear()


def test_rate_limit_blocks_after_threshold(limited_client):
    assert limited_client.get("/ping").status_code == 200
    assert limited_client.get("/ping").status_code == 200
    blocked = limited_client.get("/ping")
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After")


def test_max_body_size_rejects_large_payload(limited_client):
    response = limited_client.post("/echo", json={"payload": "x" * 100})
    assert response.status_code == 413
