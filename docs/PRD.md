# Product Requirements Document — Mini-DBaaS

**Document status:** Draft v1 · 2026-07 · Owner: Platform
**Related:** [BRD](BRD.md) · [Architecture](../ARCHITECTURE.md) · [Diagrams](diagrams.md) · [ADRs](adr/)

---

## 1. Overview

Mini-DBaaS is an API-first control plane that provisions and manages Postgres
instances as isolated Docker containers. This document specifies *what* the
product does — personas, user stories, functional and non-functional
requirements, and acceptance criteria. The *how* lives in the
[Architecture](../ARCHITECTURE.md) and [ADRs](adr/).

## 2. Personas

- **Dana, the developer** — wants a database now, for a feature branch. Doesn't
  want to learn Docker. Will forget to delete it.
- **Sam, the team lead** — accountable for the team's resource footprint. Needs
  to see what exists and stop runaway usage.
- **Priya, the platform engineer** — operates Mini-DBaaS itself. Needs backups,
  patching, and cleanup to run without her.

## 3. User stories → requirements

### 3.1 Provisioning
- **US-1:** *As Dana, I create a Postgres instance with one API call and get back
  a connection string.*
  - Async: returns `202` with a resource whose `status` transitions
    `PROVISIONING → READY`; connection details available when `READY`.
  - Size selectable (`small`/`medium`/`large`); Postgres major version selectable
    from an allowed list.
- **US-2:** *As Dana, I set a TTL so I don't have to remember to delete it.*
  - `expires_at` accepted at create or via update.
- **US-3:** *As Dana, I list and inspect my instances*, scoped to my teams,
  filterable by tag and team.

### 3.2 Lifecycle
- **US-4:** *As Dana, I resize an instance* (cpu/memory; storage where supported).
- **US-5:** *As Priya, I patch an instance to a new Postgres minor/major version*
  with data preserved and an optional pre-patch backup.
- **US-6:** *As Dana, I delete an instance*; the platform frees its port,
  optionally takes a final backup, and removes the container and volume.

### 3.3 Users, roles & credentials
- **US-7:** *As Dana, I create and drop managed database roles* inside my instance
  and grant/revoke privileges.
- **US-8:** *As Dana, I rotate the superuser credential*; the old one stops working
  and I receive the new one once.

### 3.4 Backups
- **US-9:** *As Priya, backups run automatically on a schedule* with a retention
  policy.
- **US-10:** *As Dana, I trigger a manual backup and restore from a chosen backup.*

### 3.5 Tenancy & governance
- **US-11:** *As Sam, I see all my team's instances and their quota consumption.*
- **US-12:** *As Sam, provisioning that would breach quota is rejected* with a
  clear error.
- **US-13:** *As Security, every lifecycle action is auditable* (who/what/when).

## 4. Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-1 | Create/read/update/delete Postgres instances via REST | Must |
| FR-2 | Asynchronous provisioning with pollable job status | Must |
| FR-3 | Size → resource-limit mapping; resize cpu/memory | Must |
| FR-4 | Patch (version change) preserving data | Must |
| FR-5 | Managed DB users: create/drop/grant/revoke | Must |
| FR-6 | Credential generation, one-time reveal, rotation | Must |
| FR-7 | Scheduled + manual backups; retention pruning; restore | Must |
| FR-8 | TTL expiry sweep; orphan container GC; drift reconcile | Must |
| FR-9 | Teams, memberships, roles (RBAC); ownership on all resources | Must |
| FR-10 | Quota enforcement pre-provision (count/memory/storage) | Must |
| FR-11 | Tagging (JSONB) and filtered listing | Should |
| FR-12 | Append-only audit log | Must |
| FR-13 | Thin CLI over the API for demos | Should |
| FR-14 | Pluggable provisioner interface (Docker impl; k8s-ready) | Should |

## 5. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | Performance | Time-to-ready < 60s p95; API reads < 200ms p95 |
| NFR-2 | Reliability | Control plane restart re-reconciles without manual repair; running instances survive control-plane downtime |
| NFR-3 | Security | Credentials encrypted at rest; API keys stored hashed; least-privilege DB roles; audit of all mutations |
| NFR-4 | Scalability (MVP) | ≥ 50 concurrent instances on a single host within host limits |
| NFR-5 | Observability | Structured logs, per-instance health, backup success metrics |
| NFR-6 | Portability | No control-plane logic depends on Docker specifics outside the provisioner driver |
| NFR-7 | Data safety | Configurable RPO (default 24h); restore verified in tests |

## 6. API surface (v1)

See [Architecture §3](../ARCHITECTURE.md#3-key-api-surface-v1) for the full
endpoint table. Conventions: JSON, bearer auth, `202` for async mutations with a
job resource, RFC-7807-style error bodies, cursor pagination on list endpoints.

## 7. Instance state model

`REQUESTED → PROVISIONING → READY → (PATCHING|RESIZING|BACKING_UP) → READY`
with terminal `EXPIRED`/`DELETING → DELETED` and error state `FAILED`
(recoverable via reconciler). See the [state diagram](diagrams.md#5-instance-lifecycle-state-machine).

## 8. Acceptance criteria (representative)

- **AC-1 (US-1):** `POST /v1/instances` returns `202`; polling status yields
  `READY` within 60s; the returned DSN accepts a `psql` connection.
- **AC-5 (US-5):** after patch, `SELECT version()` reports the new version and a
  pre-existing table's rows are intact.
- **AC-8 (US-8):** after rotate, the old password is rejected and the new one
  authenticates; `rotated_at` is updated; an audit row exists.
- **AC-9 (US-9):** with a nightly schedule, a fresh backup artifact exists each
  day and backups older than the retention window are pruned.
- **AC-12 (US-12):** provisioning beyond quota returns `409` with a machine-
  readable reason and creates no container.

## 9. Out of scope / future

MySQL & Oracle engines · Kubernetes/multi-host · HA & read replicas · PITR/WAL
archiving · off-host backup storage · web UI · billing/chargeback · network-level
tenant isolation. Tracked in the [Design Review roadmap](design-review.md#7-prioritized-improvement-roadmap).
