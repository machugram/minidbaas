# Mini-DBaaS — Architecture & Implementation Plan

A self-service platform that provisions and manages Postgres database instances
on demand, with lifecycle automation and multi-tenancy.

**Stack decisions (locked):** Python 3.12 + FastAPI · Docker (local, host daemon)
provisioner · Postgres as the managed engine · single-region, single-host MVP.

---

## 1. The two planes

The single most important design idea: separate the **control plane** from the
**data plane**. Everything else follows from this.

```
                        ┌─────────────────────────────────────────┐
                        │            CONTROL PLANE                  │
                        │                                           │
   client / CLI  ─────► │   FastAPI (REST)                          │
   (curl, UI)           │     ├─ auth / RBAC                        │
                        │     ├─ tenancy + quota enforcement        │
                        │     ├─ instance/user/cred handlers        │
                        │     └─ writes desired state ──┐           │
                        │                               ▼           │
                        │   Metadata DB (Postgres) ── source of     │
                        │     teams, instances, creds,   truth      │
                        │     backups, audit, quotas                │
                        │                               ▲           │
                        │   Reconciler / workers ───────┘           │
                        │     (APScheduler: backups, expiry,        │
                        │      patch jobs, drift reconcile)         │
                        └───────────────┬───────────────────────────┘
                                        │ Docker SDK
                                        ▼
                        ┌─────────────────────────────────────────┐
                        │            DATA PLANE                     │
                        │   pg-instance-<id>  (container)           │
                        │   pg-instance-<id>  (container)  ...      │
                        │   each: own volume, own port, own creds   │
                        └─────────────────────────────────────────┘
```

- **Control-plane metadata DB** is a Postgres container we run ourselves. It is
  *never* one of the tenant instances. It holds desired + observed state.
- **Data-plane instances** are the customer databases. The control plane treats
  them as cattle: it can recreate a container from `(image, volume, config)`
  without data loss because state lives in the Docker volume.
- The API only ever writes *desired state* + enqueues jobs. A **reconciler**
  makes reality match. This keeps API calls fast and makes the system
  crash-safe (restart → re-reconcile). This is the same pattern k8s uses, so it
  ports cleanly to a Kubernetes driver later.

---

## 2. Component breakdown

### 2.1 API layer (FastAPI)
- Pydantic models for every request/response; OpenAPI docs for free at `/docs`.
- Routers: `/teams`, `/instances`, `/instances/{id}/users`,
  `/instances/{id}/credentials`, `/instances/{id}/backups`, `/admin`.
- Auth: API-key or JWT bearer per principal; principal → team membership + role.
- Async endpoints; provisioning returns `202 Accepted` + a job/resource you can
  poll (`status: PROVISIONING → READY`), never blocks on Docker.

### 2.2 Provisioner (driver abstraction)
```python
class Provisioner(Protocol):
    def create(spec: InstanceSpec) -> RuntimeHandle
    def destroy(handle) -> None
    def resize(handle, new_spec) -> RuntimeHandle   # cpu/mem/storage
    def patch(handle, new_image) -> RuntimeHandle   # recreate, keep volume
    def status(handle) -> RuntimeStatus
```
- **`DockerProvisioner`** is the only impl for the MVP, but code against the
  Protocol so a `KubernetesProvisioner` can drop in later.
- Container naming: `mdbaas-pg-<instance_id>`. Volume: `mdbaas-vol-<instance_id>`.
  Labels on every container (`mdbaas.instance_id`, `mdbaas.team`, `mdbaas.managed=true`)
  so the reconciler can discover/GC reality independent of the metadata DB.
- **Port allocation:** pick from a configured range (e.g. 15000–16000), record
  the assignment in metadata, bind `127.0.0.1:<port>:5432`. (Later: connect via
  a shared pgbouncer/reverse-proxy instead of a port-per-instance.)
- **Resource limits:** map instance "size" (small/medium/large) to Docker
  `mem_limit`, `nano_cpus`, and a storage cap. Resize = update limits; for
  storage growth, recreate container against the same volume.

### 2.3 Metadata store (Postgres, SQLAlchemy + Alembic)
Core tables:
- `teams` — id, name, quotas (max_instances, max_total_memory, max_storage_gb).
- `principals` — API identities; `team_memberships` with role (owner/admin/member/readonly).
- `instances` — id, team_id, engine, version/image, size, host_port,
  desired_state, observed_state, tags (JSONB), created_at, expires_at.
- `db_users` — logical roles created *inside* an instance (app users, not principals).
- `credentials` — references to secrets (see 2.5); rotation timestamps; never plaintext.
- `backups` — id, instance_id, kind (scheduled/manual), location, size, status, created_at.
- `jobs` — async work items (provision/patch/backup/delete) with state + error, for pollable status + retries.
- `audit_log` — who did what, when, on which resource. Append-only.

### 2.4 Lifecycle automation (APScheduler in-process for MVP)
- **Backups:** per-instance cron (default nightly). A job runs `pg_dump` *inside*
  the target container via `docker exec`, streams to a backups volume/dir
  (`/backups/<instance_id>/<ts>.sql.gz`), records a `backups` row. Retention
  policy prunes old dumps (keep N / keep D days). Restore = spin new instance +
  `pg_restore`.
- **Patching:** "patch this instance" = change the image tag → `patch` on the
  provisioner recreates the container against the same volume, health-checks,
  flips `observed_state` back to READY. Optional pre-patch auto-backup. Because
  data is in the volume, this is a rolling image swap, not a migration.
- **Expiry / cleanup:** instances carry `expires_at` (TTL, esp. for ephemeral/dev
  DBs). A sweeper marks expired instances, optionally final-backups them, then
  destroys container + volume and frees the port. Also GCs orphaned containers
  (reality has a `mdbaas.managed` container with no live metadata row).
- **Drift reconcile:** periodic pass comparing desired vs. Docker reality;
  restarts crashed containers, recreates missing ones.

### 2.5 Credentials & secrets
- On create, generate a strong superuser password; hand it back **once** in the
  create response, then store only an encrypted copy (Fernet/libsodium with a
  key from env/secrets file — pluggable to Vault later).
- `POST /instances/{id}/credentials:rotate` → generate new password, `ALTER ROLE
  ... PASSWORD` inside the instance, re-encrypt, bump `rotated_at`, audit it.
- Managed `db_users` endpoints: create/drop roles, grant/revoke, set per-user
  passwords — all executed as SQL inside the target instance over a short-lived
  admin connection.

### 2.6 Multi-tenancy, quotas, RBAC
- Every resource is owned by a **team**. Principals act within teams via a role.
- **Quota enforcement** happens in the API *before* provisioning: check the
  team's current counts/sums against limits; reject with `409` if exceeded.
- **Tags** (JSONB) on instances for cost/ownership grouping and filtered listing.
- Isolation for the MVP is process/container-level (separate containers, separate
  creds, localhost-only ports). Network isolation (per-team Docker networks) is a
  documented next step.

---

## 3. Key API surface (v1)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/teams` | create team (admin; creator is enrolled as owner) |
| GET | `/v1/teams` | list teams (scoped to caller; all for admin) |
| GET | `/v1/teams/{id}` | team detail |
| GET | `/v1/teams/{id}/usage` | current quota consumption |
| GET | `/v1/teams/{id}/members` | list members + roles |
| POST | `/v1/teams/{id}/members` | add a principal to the team (admin+) |
| DELETE | `/v1/teams/{id}/members/{principal_id}` | remove a member (admin+) |
| POST | `/v1/principals` | create an API identity + one-time key (platform admin) |
| GET | `/v1/me` | current principal + team memberships/roles |
| POST | `/v1/instances` | provision instance (async → 202) |
| GET | `/v1/instances` | list (scoped to caller's teams; filter by tag/team) |
| GET | `/v1/instances/{id}` | detail + live status |
| PATCH | `/v1/instances/{id}` | resize / retag / set expiry |
| DELETE | `/v1/instances/{id}` | deprovision (optional final backup) |
| POST | `/v1/instances/{id}/resize` | resize cpu/memory (async → 202) |
| POST | `/v1/instances/{id}/patch` | patch to new engine version |
| POST | `/v1/instances/{id}/retry` | recover a FAILED instance (re-run last failed job) |
| GET | `/v1/instances/{id}/status` | poll provisioning/patch job state |
| GET | `/v1/instances/{id}/jobs` | job queue history for this instance |
| POST | `/v1/instances/{id}/users` | create managed DB role |
| GET | `/v1/instances/{id}/users` | list managed DB roles |
| DELETE | `/v1/instances/{id}/users/{name}` | drop role |
| POST | `/v1/instances/{id}/credentials/rotate` | rotate superuser password |
| POST | `/v1/instances/{id}/backups` | trigger manual backup |
| GET | `/v1/instances/{id}/backups` | list backups |
| POST | `/v1/instances/{id}/restore` | restore from a backup |
| GET | `/v1/audit` | audit trail (scoped to caller's teams; filter by team) |

---

## 4. Repo layout

```
minidbaas/
├── docker-compose.yml          # control-plane API + metadata Postgres
├── pyproject.toml
├── .env.example
├── app/
│   ├── main.py                 # FastAPI app factory, router wiring
│   ├── config.py               # pydantic-settings
│   ├── db.py                   # engine/session, Alembic wiring
│   ├── models/                 # SQLAlchemy models
│   ├── schemas/                # Pydantic request/response
│   ├── api/                    # routers: teams, instances, users, creds, backups
│   ├── auth/                   # api-key/JWT, RBAC dependency
│   ├── provisioner/
│   │   ├── base.py             # Provisioner Protocol + specs
│   │   └── docker_driver.py    # DockerProvisioner
│   ├── lifecycle/
│   │   ├── scheduler.py        # APScheduler setup
│   │   ├── backups.py          # pg_dump / prune / restore
│   │   ├── patching.py
│   │   └── reaper.py           # expiry + orphan GC + drift reconcile
│   ├── secrets/                # encryption helpers
│   └── audit.py
├── migrations/                 # Alembic
├── tests/                      # pytest (unit + integration w/ real Docker)
└── cli/                        # optional thin click/typer client for demos
```

---

## 5. Phased build plan

Each phase folds in the correctness/security hardening the
[Design Review](docs/design-review.md) flagged as cheap enough to do *now* rather
than defer — marked **[DR-n]** against roadmap item _n_ in
[design-review §7](docs/design-review.md#7-prioritized-improvement-roadmap). These
are not "later"; they are part of the definition-of-done for the phase they live
in, because each one prevents a real race, leak, or data-loss bug.

**Phase 0 — Skeleton**
compose (API + metadata PG), config, DB session, Alembic baseline, `/health`,
one `teams` endpoint end-to-end. Proves the plumbing.

**Phase 1 — Provisioning core** *(the "core CRUD" milestone)*
`DockerProvisioner.create/destroy/status`, `instances` table, `jobs` for async
status, POST/GET/DELETE `/instances`. You can create a real Postgres and connect
to it.
- **[DR-1]** Port allocation is atomic in the DB (`ports` table +
  `UPDATE ... WHERE instance_id IS NULL RETURNING port`); never bind before commit.
- **[DR-3]** Job claiming via `SELECT ... FOR UPDATE SKIP LOCKED`, with
  `claimed_at` visibility timeout, bounded retries + backoff, and a terminal
  `FAILED`/dead-letter. Every provisioner action is idempotent and keyed by
  instance id.
- **[DR-2]** The reconciler acts on `desired_state`, never on container presence,
  and takes the `jobs` row as a per-instance lock — so a delete-in-progress is
  never resurrected.
- **[DR-6]** `POST /instances` honours an `Idempotency-Key` header so a retried
  create can't spawn a second database.

**Phase 2 — Tenancy & quotas**
teams/principals/roles, auth dependency, quota checks in the create path, tags,
scoped listing, audit log.
- **[DR-5]** API keys stored as argon2/bcrypt hashes (verify, never recover).
- **[DR-8]** Quota model documents that `max_storage_gb` is a *soft* limit under
  the default volume driver, and records the MVP tenant-isolation level honestly
  (shared kernel/daemon) rather than implying stronger guarantees.

**Phase 3 — Credentials & DB users**
password generation + encryption (one-time reveal, encrypted-at-rest per
[ADR-005](docs/adr/ADR-005-secrets-encrypted-in-metadata-db.md)), rotation
endpoint, managed `db_users` create/drop/grant.

**Phase 4 — Lifecycle automation**
APScheduler; nightly backups + retention; manual backup + restore; `patch`
workflow (image swap, keep volume); orphan GC + drift reconcile.
- **[DR-4]** Every scheduled tick is guarded by a Postgres advisory lock keyed by
  job type, so running >1 API replica can't double-fire backups.
- **[DR-7]** Expiry is two-phase: at `expires_at` the instance is **stopped** (not
  destroyed) and the owner notified; destroy + final backup happen only after a
  grace window, with a one-click "extend TTL."

**Phase 5 — Polish**
resize (mem/cpu limits; disruption disclosed in the response), typer CLI for
demos, README with a scripted end-to-end walkthrough, integration tests against
real Docker, structured logging + metrics.

> The remaining Design Review items (off-host backups, socket-proxy, host-level
> backpressure, observability pack, k8s driver, pgbouncer/TLS, PITR, Vault) are
> deliberately *not* in the MVP — they are the v1.1 and v2 roadmap in
> [design-review §7](docs/design-review.md#7-prioritized-improvement-roadmap).

---

## 6. Notable risks / decisions to revisit

**Hardened inside the MVP** (see the phase plan's [DR-n] markers — these were
tempting to defer but are cheap enough to just do right):
- Port-allocation race → atomic DB allocation **[DR-1]**.
- Reconciler resurrecting a deleted DB → desired-state-keyed + job locking **[DR-2]**.
- Lost/double job execution → `SKIP LOCKED` claim + idempotent, retried handlers **[DR-3]**.
- Scheduler double-firing under replicas → advisory-lock per tick **[DR-4]**.
- Reversible API keys → hashed at rest **[DR-5]**.
- Duplicate create on retry → `Idempotency-Key` **[DR-6]**.
- Data loss on expiry → two-phase stop → grace → destroy + final backup **[DR-7]**.

**Accepted MVP limitations, documented not hidden** (roadmap in
[design-review §7](docs/design-review.md#7-prioritized-improvement-roadmap)):
- **Docker socket access** = root-equivalent power; the trust boundary is the
  single host. Fast-follow: socket-proxy + rootless daemon.
- **Storage quotas are soft** under the default volume driver **[DR-8]**; real
  enforcement needs XFS/ZFS quotas.
- **Backups sit on the same host as the data** in the MVP; off-host object
  storage is the first fast-follow — until then this is a copy, not a backup.
- **Port-per-instance** doesn't scale and is plaintext; path is a shared
  pgbouncer/SNI proxy with TLS.
- **In-process APScheduler** still pauses schedules if the single process is down
  (advisory lock only fixes double-firing, not downtime); path is a durable
  worker or k8s CronJobs.
- **Secrets encrypted with an env-held key**; envelope encryption + Vault/KMS is
  the upgrade.
- Everything is single-host; HA/multi-region is explicitly out of scope.

---

## 7. Why this maps to the JD
"Self-service workflows for configuration, patching, deployment, lifecycle
management" → the API *is* the self-service layer; `patch`, backups, expiry, and
the reconciler *are* the lifecycle automation; teams/quotas/tags *are* the
multi-tenant operational model. The control/data-plane split + driver abstraction
is the piece that shows systems-design maturity beyond a CRUD app.
