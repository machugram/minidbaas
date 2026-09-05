"""Health, readiness, and metrics endpoints."""


def test_health_liveness(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_health_readiness(client):
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"]["status"] == "ok"
    # Scheduler is disabled in the test harness.
    assert body["checks"]["scheduler"]["status"] == "skipped"


def test_metrics(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "mdbaas_jobs_queued" in response.text
    assert "mdbaas_jobs_oldest_queued_age_seconds" in response.text
    assert "mdbaas_info" in response.text


def test_http_requests_metric_increments(client):
    client.get("/health")
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert 'mdbaas_http_requests_total{method="GET",path="/health",status="200"}' in metrics.text
