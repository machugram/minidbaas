# Design Review — Mini-DBaaS

A deliberately adversarial read of the [Architecture](../ARCHITECTURE.md). The
goal is to name the weak points *before* they're built, separate "fine for an
MVP" from "will bite you," and give a prioritized path to a system that would
survive real use. Findings are grouped by theme, each with **Severity**,
**Why it matters**, and **Recommendation**. A consolidated roadmap is in §7.

Legend — Severity: 🔴 high · 🟠 medium · 🟡 low/tracking.

---

## 1. Security & isolation

### 1.1 🔴 The Docker socket is root-equivalent
The control plane talks to `/var/run/docker.sock`. Anyone who can reach that
socket — including a compromised API process — can start a privileged container
and own the host. The architecture notes this as a "trust boundary" but doesn't
constrain it.
- **Recommendation:** don't mount the raw socket into the API. Put a
  **docker-socket-proxy** (e.g. `tecnativa/docker-socket-proxy`) in front,
  allowing only the specific endpoints the provisioner uses
  (`containers`, `volumes`, `exec`), deny `POST /containers/*/exec` privileged
  options, and run the daemon **rootless**. On the k8s driver this becomes a
  scoped ServiceAccount + PodSecurity admission.

### 1.2 🔴 Tenant isolation is only container-deep
All instances share one kernel and one daemon. A container escape, or simply a
noisy neighbor, crosses tenant boundaries. There is no network policy, so any
container can reach any other and the host.
- **Recommendation (roadmap):** per-team Docker networks so instances can't see
  each other; drop all capabilities + `no-new-privileges` + read-only rootfs on
  managed containers; for real isolation, gVisor/Kata runtime or one VM per team.
  Document the MVP isolation level honestly in the BRD.

### 1.3 🟠 Secrets: single Fernet key in env is a thin envelope
Encrypting credentials with one symmetric key loaded from env means a leak of
(metadata DB dump **+** environment) exposes every credential. There's also no
key rotation story.
- **Recommendation:** envelope encryption — a per-instance data key wrapped by a
  KEK, so KEK rotation doesn't require re-encrypting every row; move the KEK to
  Vault Transit or a cloud KMS. Store **API keys hashed** (argon2/bcrypt), never
  reversibly. Keep the "reveal superuser password once" behavior — that part is
  right.

### 1.4 🟠 Plaintext Postgres wire protocol
Instances bind `127.0.0.1` for the MVP, which is defensible, but the moment
connectivity goes cross-host (the pgbouncer/proxy roadmap item), traffic is
unencrypted.
- **Recommendation:** terminate TLS at the proxy or issue per-instance server
  certs; require `sslmode=verify-full` in emitted DSNs once off-host.

---

## 2. Correctness & concurrency

### 2.1 🔴 Port allocation is a race
"Pick a free port from a range and record it" is a classic TOCTOU: two
concurrent creates can select the same port before either row is written.
- **Recommendation:** allocate ports in the database, not in application memory —
  a `ports(port PK, instance_id UNIQUE NULL)` table with an atomic
  `UPDATE ... WHERE instance_id IS NULL RETURNING port`, or a Postgres advisory
  lock around allocation. Never bind before the row commits.

### 2.2 🔴 Reconciler vs. API delete race — risk of resurrection
The drift reconciler "recreates missing containers." If it observes a container
that the API is mid-deleting (container gone, row not yet terminal), it can
**recreate a database the user just deleted**.
- **Recommendation:** the reconciler must act on **desired_state**, never on
  presence alone. `desired=DELETED` ⇒ ensure-absent; `desired=READY` ⇒
  ensure-present. Use the `jobs` row as a lock so exactly one actor mutates an
  instance at a time, and make every provisioner action idempotent + keyed by
  instance id.

### 2.3 🟠 The `jobs` table is a queue — treat it like one
Polling a table is fine, but the design doesn't specify delivery semantics.
Without care you get lost jobs (worker dies mid-run) or double execution.
- **Recommendation:** at-least-once with idempotent handlers; `claimed_at` +
  visibility timeout so a crashed worker's job is re-claimed; bounded retries
  with exponential backoff; a terminal `FAILED` + dead-letter for human review.
  `SELECT ... FOR UPDATE SKIP LOCKED` gives you a safe multi-worker claim on
  Postgres without extra infra.

### 2.4 🟠 In-process scheduler doesn't survive replication
APScheduler in the API process means: (a) if you run two API replicas you get
**double backups**; (b) if the single process is down, schedules silently don't
fire.
- **Recommendation (MVP-acceptable, documented):** keep APScheduler but guard
  every scheduled tick with a Postgres advisory lock so only one instance acts.
  Roadmap: a dedicated worker (arq/Celery/RQ + Redis) or k8s CronJobs. Covered in
  [ADR-006](adr/ADR-006-in-process-scheduler.md).

### 2.5 🔴 Drift detection treats "present" as "healthy" — a stopped container is invisible
Observed live: a container was stopped outside the platform (host/Docker restart
with no restart policy engaging), yet the instance stayed `observed_state=READY`
indefinitely — nobody, human or reconciler, ever noticed. The root cause is in
`reconcile()` ([reconciler.py](../app/lifecycle/reconciler.py)): it only checks
`instance.id in reality`, and `reality` comes from `list_managed()`, which
intentionally lists containers `all=True` (stopped included) — a deliberate choice,
because both orphan-GC and the two-phase-expiry `STOPPED` state (DR-7) need a
stopped-but-labelled container to still count as "present" so it isn't destroyed or
mistaken for missing. That correct choice has a side effect: a container that stops
*unexpectedly* while `observed_state=READY` is just as invisible as one that stopped
*intentionally*, and the platform reports a database as healthy while it is actually
down.
- **Recommendation:** for instances where `desired_state=READY`,
  `observed_state=READY`, and the container is *present*, additionally check
  `provisioner.status(instance.id) == RUNNING` (one extra call per READY instance
  per tick — cheap at the ≤50-instance MVP scale in NFR-4). If not running, treat it
  as drift: `provisioner.start()` first, and if it doesn't come back healthy,
  escalate to the same re-provision path as a missing container. This is a
  reconciler-logic change only — `list_managed()`/the orphan-GC contract stays
  correct as-is.

---

## 3. Data durability & backups

### 3.1 🔴 Backups live on the same host as the data
A local `/backups` volume means a host/disk loss takes the databases **and** their
backups. That is not a backup — it's a copy.
- **Recommendation:** push artifacts to off-host object storage (S3/MinIO) as the
  default; keep local as a cache. Record a checksum and verify on restore.

### 3.2 🟠 Logical dumps give no PITR and scale poorly
`pg_dump` is simple and version-tolerant (running it *inside* the container uses
the instance's own binaries — good call, avoids client/server skew). But it
can't do point-in-time recovery, and it's slow/locking-ish on large DBs.
- **Recommendation:** fine for the MVP; state the RPO honestly (= backup
  interval, so up to 24h of loss). Roadmap: WAL archiving + base backups via
  `pgBackRest`/`wal-g` for PITR and faster large-DB backups.

### 3.3 🟠 Restore has no verification loop
Backups you never test are Schrödinger's backups.
- **Recommendation:** a periodic "restore into a scratch instance and run a
  smoke query" job; surface last-verified-restore per instance. Make it an
  acceptance test (PRD AC-1/AC-9 territory).

---

## 4. Resource management

### 4.1 🔴 Storage quotas are largely unenforceable with the default volume driver
`mem_limit` and `nano_cpus` are real and enforced. **Storage is not** — the
`local` volume driver has no size cap, so `max_storage_gb` in the quota model is
aspirational and a tenant can fill the host disk.
- **Recommendation:** be honest that storage limits are soft in the MVP
  (monitor + alert + reject new writes at the app layer is not possible for a raw
  PG). Real options: XFS project quotas on the volume path, loopback-mounted
  ext4 images per instance, or ZFS datasets with quotas. Pick one before
  claiming storage isolation.

### 4.2 🟠 No host-level backpressure
Team quotas cap per-team usage, but N teams each within quota can still
oversubscribe one host. Nothing rejects the (N+1)th instance when the host is
full.
- **Recommendation:** a global admission check against host memory/CPU/disk
  headroom in the create path; reject with `503`/`409` and a clear reason.

### 4.3 🟡 Resize semantics need pinning down
CPU/memory resize can be a live `update`; storage "resize" implies recreate.
Recreate means a brief outage — that must be explicit in the API contract, not a
surprise.
- **Recommendation:** document which resizes are online vs. disruptive; return
  the expected disruption in the resize response.

---

## 5. Availability & operability

### 5.1 🟠 Metadata DB is a control-plane SPOF (but data plane isn't — good)
If the metadata Postgres dies, no new operations succeed. The saving grace,
correctly designed, is that **running instances keep serving** because they're
decoupled — that decoupling is the strongest part of the design and should be
stated as a feature.
- **Recommendation:** back up the metadata DB itself (it's the crown jewels);
  document RTO for control-plane recovery; managed-Postgres or a replica on the
  roadmap.

### 5.2 🟠 Observability is under-specified
"Structured logging + metrics" is listed but not designed. You can't operate a
fleet you can't see.
- **Recommendation:** per-instance health (is the container up *and* is Postgres
  accepting connections), `postgres_exporter` sidecar or scrape, backup
  success/age gauges, job queue depth/latency, and an SLO on time-to-ready.

### 5.3 🟡 Expiry auto-destroy is dangerous by default
Auto-deleting at `expires_at` will eventually delete something someone still
needed.
- **Recommendation:** two-phase — **stop** (not destroy) at expiry, notify the
  owner, then destroy after a grace window with a final backup. Allow one-click
  "extend TTL." This is a product safety feature, not just an ops detail.

---

## 6. API & product gaps

- 🟠 **Idempotency of create:** a retried `POST /instances` (client timeout) must
  not create two DBs. Support an `Idempotency-Key` header.
- 🟠 **Pagination & filtering** are implied but must be specified for list
  endpoints (cursor-based; filter by team/tag/state) or they'll be retrofitted
  painfully.
- 🟡 **Async contract clarity:** `202 + poll` is right; also emit events/webhooks
  on state change so clients don't busy-poll (roadmap).
- 🟡 **Soft vs. hard delete:** deleting a DB destroys data. Consider a short
  soft-delete window (row retained, volume kept) before irreversible purge.
- 🟡 **RBAC granularity:** the four roles are a fine start; note that
  cross-team admin (platform operators) needs a distinct super-role with its own
  audit treatment.

---

## 7. Prioritized improvement roadmap

Ordered by (risk reduced ÷ effort). The first block is small and high-leverage —
worth doing *inside* the MVP rather than deferring. **These eight are now folded
into the [Architecture phase plan](../ARCHITECTURE.md#5-phased-build-plan) as
`[DR-n]` done-criteria** (DR-1/2/3/6 → Phase 1, DR-5/8 → Phase 2, DR-4/7 →
Phase 4), so they are part of building the MVP, not a follow-up.

**Do within the MVP (cheap, prevents real bugs)**
1. DB-backed atomic **port allocation** (§2.1).
2. Reconciler keyed on **desired_state** + `jobs`-row locking (§2.2).
3. `FOR UPDATE SKIP LOCKED` job claiming + retries/backoff (§2.3).
4. Advisory-lock guard on scheduled ticks (§2.4).
5. **Hash API keys**; keep one-time credential reveal (§1.3).
6. `Idempotency-Key` on create (§6).
7. Two-phase **expiry (stop → grace → destroy + final backup)** (§5.3).
8. Honest docs on **storage-quota softness** and **isolation level** (§4.1, §1.2).

**Fast-follow (v1.1)** — item 9 was found *after* the MVP shipped (observed live
while operating the system), not part of the original DR-1..8 set; the rest are
unchanged from the original review.
9. **Reconciler must check `RUNNING`, not just presence** (§2.5) — cheap (one
   `status()` call per READY instance per tick) and closes a real false-positive:
   today a stopped-but-labelled container reports as healthy indefinitely.
10. **Off-host backup storage** (S3/MinIO) + restore verification job (§3.1, §3.3).
11. **docker-socket-proxy** + rootless daemon + hardened container flags (§1.1, §1.2).
12. Host-level **admission/backpressure** check (§4.2).
13. Observability pack: health, `postgres_exporter`, backup/job metrics (§5.2).

**Roadmap (v2+)**
14. **Kubernetes provisioner** behind the existing Protocol (the driver
    abstraction is what makes this cheap — [ADR-002](adr/ADR-002-provisioner-driver-abstraction.md)).
15. Shared **pgbouncer/SNI proxy** replacing port-per-instance, with TLS (§1.4).
16. **PITR** via WAL archiving (`pgBackRest`/`wal-g`) (§3.2).
17. Envelope encryption via Vault/KMS (§1.3).
18. Real tenant isolation (per-team networks, gVisor/Kata, or VM-per-team) (§1.2).
19. Multi-engine (MySQL) via a second driver; HA/replicas for tenant DBs.

---

## 8. What the design already gets right

Not everything needs fixing — these are load-bearing good decisions worth
keeping:

- **Control/data-plane split** with running instances surviving control-plane
  downtime — the single best structural choice ([ADR-001](adr/ADR-001-control-plane-data-plane-split.md)).
- **Desired-state + reconciler** instead of imperative do-it-now calls — makes
  the system crash-safe and self-healing ([ADR-003](adr/ADR-003-desired-state-reconciler.md)).
  Self-healing today covers *missing* containers; §2.5 is the gap where a
  *stopped-but-present* one still slips through.
- **Provisioner Protocol** isolating Docker so a k8s driver is additive, not a
  rewrite ([ADR-002](adr/ADR-002-provisioner-driver-abstraction.md)).
- **Patch = image swap on a persistent volume** — honest, simple, and correct
  given data lives in the volume ([ADR-004](adr/ADR-004-patch-via-image-swap.md)).
- **Labels as ground truth** for discovery/GC, independent of the metadata DB —
  the right way to reconcile drift.
- **One-time credential reveal + encryption at rest** — correct instinct even if
  the key management needs hardening.
