# Mini-DBaaS

A self-service platform that provisions and manages **Postgres instances on
demand** — create/delete/resize, managed roles, credential rotation, automated
backups, patching, and TTL cleanup — with team-based multi-tenancy and quotas.

The control plane is a FastAPI service; tenant databases run as Docker containers.
Design docs live in [`docs/`](docs/README.md); this README is how to run it.

```
CLI / curl ──▶ FastAPI (routers → services)
                    │ writes desired state + jobs
                    ▼
              Metadata DB ◀── APScheduler (worker · reconciler · reaper · backups)
                    │
              DockerProvisioner ──tcp──▶ docker-socket-proxy ──▶ host Docker
                                              │
                    mdbaas-pg-<id> · mdbaas-vol-<id> · (optional mdbaas-net-<team>)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for the control/data-plane split (ADR-001),
the desired-state reconciler (ADR-003), and the provisioner abstraction (ADR-002).

## Layout

| Path | What |
|---|---|
| `app/api/` | HTTP routers (thin: validate, authenticate, delegate) |
| `app/services/` | use-cases: enforce policy, write desired state, enqueue jobs |
| `app/lifecycle/` | workers: job queue, reconciler, backups, reaper, scheduler |
| `app/provisioner/` | the only code that talks to Docker (`docker_driver.py`) |
| `app/models.py` | metadata schema (source of truth) |
| `cli/` | thin HTTP client for demos |
| `tests/` | pytest suite; runs Docker-free via a fake provisioner |

## Run it (Docker)

```bash
# 1. Generate the credential-encryption key (ADR-005)
export MDBAAS_CREDENTIAL_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# 2. Start the control plane (API + metadata Postgres)
docker compose up --build
```

The API is published on host port **8001** by default (`docker-compose.yml`). If
that's taken, override it: `MDBAAS_HOST_PORT=8010 docker compose up --build`.

On first start the API logs a one-time **bootstrap admin API key** — grab it:

```bash
docker compose logs api | grep "bootstrap admin API key"
export MDBAAS_API_KEY=mdb_...      # from the log line
export MDBAAS_API_URL=http://localhost:8001
```

Interactive docs (web): http://localhost:8001/docs

### Terminal walkthrough — plain curl (no install needed)

```bash
curl -s "$MDBAAS_API_URL/health"          # liveness
curl -s "$MDBAAS_API_URL/health/ready"   # readiness (DB + Docker + scheduler)
curl -s "$MDBAAS_API_URL/metrics"         # Prometheus metrics

curl -s -X POST "$MDBAAS_API_URL/v1/teams" \
  -H "Authorization: Bearer $MDBAAS_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"acme"}'
TEAM=<id-from-response>

curl -s -X POST "$MDBAAS_API_URL/v1/instances" \
  -H "Authorization: Bearer $MDBAAS_API_KEY" -H "Content-Type: application/json" \
  -d "{\"team_id\":\"$TEAM\",\"name\":\"app-db\",\"size\":\"small\"}"
DB=<id-from-response>

curl -s "$MDBAAS_API_URL/v1/instances/$DB/status" -H "Authorization: Bearer $MDBAAS_API_KEY"
curl -s -X POST "$MDBAAS_API_URL/v1/instances/$DB/backups" -d '{}' \
  -H "Authorization: Bearer $MDBAAS_API_KEY" -H "Content-Type: application/json"
curl -s -X DELETE "$MDBAAS_API_URL/v1/instances/$DB" -H "Authorization: Bearer $MDBAAS_API_KEY"
```

### Optional: install the `mdbaas` CLI

The CLI (`cli/mdbaas.py`) is a thinner wrapper over the same endpoints — install it
if you'd rather type `mdbaas instances create ...` than curl:

```bash
python3 -m venv .venv
# cryptography/bcrypt ship as source sdists on some platforms without a matching
# wheel; force prebuilt binaries to avoid needing a Rust/C toolchain.
.venv/bin/pip install --only-binary=:all: cryptography bcrypt
.venv/bin/pip install -e .
source .venv/bin/activate
```

```bash
mdbaas health
mdbaas teams create acme
mdbaas instances create app-db --team <team-id> --size small
mdbaas instances status <instance-id>          # poll until observed_state == ready
mdbaas backups create <instance-id>             # manual backup (pg_dump)
mdbaas instances rotate <instance-id>           # rotate superuser credential
mdbaas instances delete <instance-id>           # async delete (final backup by default)
```

## Develop & test

```bash
python -m venv .venv && source .venv/bin/activate
pip install ".[dev]"
pytest                # unit suite, no Docker required
```

The suite uses a file-backed SQLite metadata DB and a `FakeProvisioner`, so it
exercises the real API, services, job queue, and reconciler without spinning up
containers. `MDBAAS_ENABLE_SCHEDULER=false` keeps the background scheduler off
during tests.

## Configuration

All settings use the `MDBAAS_` prefix — see [`.env.example`](.env.example). The
important ones: `MDBAAS_DATABASE_URL`, `MDBAAS_CREDENTIAL_ENCRYPTION_KEY`,
`MDBAAS_DOCKER_BASE_URL` (points at `docker-socket-proxy` in compose),
`MDBAAS_PG_IMAGE_DEFAULT`, and the `MDBAAS_PORT_RANGE_*` for the ADR-007 port pool.

### Security & hardening

- **Docker socket proxy** — compose runs `tecnativa/docker-socket-proxy` so the API
  never mounts the raw socket (design-review §1.1).
- **Per-team networks** — each team's instances attach to an isolated bridge network
  (`MDBAAS_PER_TEAM_NETWORKS`, default `true`).
- **Envelope encryption** — credentials use a per-secret DEK wrapped by the KEK;
  set `MDBAAS_CREDENTIAL_ENCRYPTION_KEY_PREVIOUS` during KEK rotation.
- **API hardening** — rate limiting (`MDBAAS_RATE_LIMIT_*`), request size cap
  (`MDBAAS_MAX_REQUEST_BYTES`), optional CORS (`MDBAAS_CORS_ORIGINS`), Prometheus
  request counter (`mdbaas_http_requests_total`).

## License

MIT — see [LICENSE](LICENSE).

## Status & limitations

This is an MVP. The correctness/security hardening from the
[design review](docs/design-review.md) that is *cheap enough to do now* is built
in — atomic port allocation, desired-state reconciliation, `SKIP LOCKED` job
claiming, advisory-locked schedules, hashed API keys, idempotent create, two-phase
expiry. The documented **accepted limitations** (soft storage quotas, on-host
backups, port-per-instance, single-host scheduler) and the v1.1/v2 roadmap are in
[design-review §6–7](docs/design-review.md#7-prioritized-improvement-roadmap).
