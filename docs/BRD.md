# Business Requirements Document — Mini-DBaaS

**Document status:** Draft v1 · 2026-07 · Owner: Platform
**Related:** [PRD](PRD.md) · [Architecture](../ARCHITECTURE.md) · [ADRs](adr/)

---

## 1. Purpose

Engineering teams routinely need throwaway and long-lived Postgres databases for
development, testing, demos, and services. Today this is done by hand: someone
runs a container or clicks through a cloud console, credentials are shared over
chat, nobody tracks who owns what, and databases linger long after they are
needed — consuming resources and creating security exposure.

Mini-DBaaS is an internal **self-service platform** that lets a team provision,
operate, and retire Postgres instances through an API, with ownership, quotas,
backups, patching, and automated cleanup handled by the platform rather than by
people.

## 2. Business context & drivers

| Driver | Current pain | Desired outcome |
|---|---|---|
| **Provisioning speed** | Manual, tribal-knowledge setup; hours to a usable DB | Self-service DB in under a minute via one API call |
| **Ownership & cost** | No record of who owns a DB or why; zombie databases | Every DB owned by a team, tagged, quota-bounded |
| **Security** | Credentials shared in plaintext; long-lived and unrotated | Generated, encrypted, rotatable credentials; least-privilege roles |
| **Operational toil** | Backups and patching done ad hoc or not at all | Automated backups, one-click patching, TTL cleanup |
| **Governance** | No audit trail of database operations | Append-only audit log of every lifecycle action |

## 3. Objectives & success metrics

- **O1 — Self-service:** a developer provisions a working DB with a single
  authenticated API call. *Metric:* time-to-ready < 60s (p95).
- **O2 — Accountability:** 100% of instances are attributable to a team and
  appear against that team's quota.
- **O3 — Data safety:** every long-lived instance has an automated backup no
  older than its configured RPO (default 24h). *Metric:* backup success rate ≥ 99%.
- **O4 — Reduced waste:** ephemeral instances are automatically reclaimed at
  expiry. *Metric:* zero instances alive > 24h past `expires_at`.
- **O5 — Auditability:** every create/delete/resize/patch/rotate action is
  recorded with actor, target, and timestamp.

## 4. Stakeholders

| Stakeholder | Interest |
|---|---|
| **Developers (end users)** | Fast, no-ticket access to databases |
| **Team leads** | Visibility and control of their team's DB footprint and cost |
| **Platform/SRE** | A safe, automated system that reduces manual ops and enforces policy |
| **Security** | Credential hygiene, isolation, and an audit trail |

## 5. Scope

**In scope (MVP):** Postgres only; Docker-based provisioning on a single host;
create/delete/resize/patch; managed DB users/roles; credential generation and
rotation; automated + manual backups and restore; TTL expiry and orphan cleanup;
team-based ownership, quotas, and tagging; audit logging; REST API + thin CLI.

**Out of scope (MVP):** MySQL/Oracle and other engines; Kubernetes/multi-host and
multi-region; high availability / replication / failover of tenant DBs;
point-in-time recovery; a web UI; billing/chargeback; VPC/network-level tenant
isolation; BYO-cloud. These are captured as a future roadmap.

## 6. Business rules

- **BR1** — A database instance belongs to exactly one team; a user may belong to
  multiple teams.
- **BR2** — Provisioning is rejected if it would exceed the owning team's quota
  (instance count, total memory, or total storage).
- **BR3** — Superuser credentials are shown exactly once, at creation; thereafter
  the platform stores only an encrypted copy.
- **BR4** — Destructive actions (delete, expiry) must be attributable and, for
  data-bearing instances, should capture a final backup unless explicitly skipped.
- **BR5** — Every lifecycle action is recorded in an append-only audit log.
- **BR6** — Only members with an appropriate role may mutate an instance
  (owner/admin write; member limited; readonly none).

## 7. Assumptions & constraints

- Single trusted host with a Docker daemon; operators of the platform are trusted.
- Network reachability to instances is local/VPN for the MVP (no public exposure).
- Postgres is the only supported engine at launch; the design must not preclude
  adding engines later (see [ADR-002](adr/ADR-002-provisioner-driver-abstraction.md)).

## 8. Risks (business view)

| Risk | Impact | Mitigation |
|---|---|---|
| Data loss on expiry/delete | High | Final backup + grace period before destroy |
| Credential leakage | High | Encryption at rest, rotation, one-time reveal |
| Resource exhaustion by one team | Medium | Quotas + host-level caps |
| Single-host outage | Medium | Documented as accepted MVP limitation; HA on roadmap |
| Backup on same host as data | High | Roadmap: off-host object storage for backups |

See the [Design Review](design-review.md) for the engineering-level scrutiny of
these risks.
