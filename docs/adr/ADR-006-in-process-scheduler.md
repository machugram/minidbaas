# ADR-006: In-process scheduler for lifecycle automation

**Status:** Accepted
**Date:** 2026-07
**Owners:** Platform
**Related:** [ADR-003](ADR-003-desired-state-reconciler.md)

## Status Quo

Backups, TTL expiry, orphan garbage-collection, and drift reconciliation all need
to run on a schedule, unattended. There is a spectrum of ways to run recurring
work: an in-process scheduler library, a dedicated worker with a durable queue
(Celery/RQ/arq + Redis), or platform-native cron (system cron, Kubernetes
CronJobs). We need to pick one for the MVP without painting ourselves into a
corner.

## Goal

Run scheduled lifecycle jobs reliably enough for a single-host MVP with the least
operational surface area, while keeping the door open to a more robust,
horizontally-scalable execution model later.

## Non-Goal

This ADR does not deliver a highly-available, multi-node job execution system,
and it does not guarantee that schedules fire while the control-plane process is
down. Exactly-once execution is explicitly *not* promised — handlers are made
idempotent instead (see [ADR-003](ADR-003-desired-state-reconciler.md)).

## Proposed Solution

We use **APScheduler running inside the API process** for the MVP, and we harden
it against the two failure modes that actually matter with a single guard.

**One process, no extra infrastructure.** APScheduler ticks the backup, expiry,
GC, and reconcile jobs on their configured intervals within the same container
that serves the API. There is no Redis, no broker, no separate worker deployment —
which keeps the local demo to a single `docker-compose up` and matches the
single-host scope of the MVP.

**A Postgres advisory lock guards every tick.** The [Design Review §2.4](../design-review.md#correctness--concurrency)
identifies the real risk: if the API is ever run with more than one replica,
every replica's scheduler fires and you get **double backups**. Wrapping each
scheduled tick in a `pg_try_advisory_lock` keyed by job type means only one
replica does the work even if several are running — so scaling the API for
availability doesn't corrupt scheduled work. Jobs still execute through the same
`jobs`-table claiming (`FOR UPDATE SKIP LOCKED`) and idempotent handlers used by
on-demand work, so a crashed tick is retried, not lost.

**The known limitation is stated, not hidden:** if the single process is down,
schedules simply don't fire during that window (a backup may be skipped, an
expiry delayed). For an MVP with a 24h default RPO this is acceptable, and the
mitigation is explicitly on the roadmap.

**The upgrade path is pre-decided.** Because job *definitions* live in the DB and
handlers are idempotent, moving to a dedicated worker (arq/Celery) or Kubernetes
CronJobs later is a change to the *trigger*, not the *work* — the same handlers
run, just invoked by a more robust scheduler.

## Alternatives

### Dedicated worker + durable queue (Celery/RQ/arq + Redis) now

The production-grade answer: survives API restarts, scales horizontally, gives
real retry/visibility. Rejected for the MVP because it adds a broker and a second
deployable, contradicting the single-host, single-command demo goal. It is the
designated v1.1+ upgrade, made cheap by the idempotent-handler design.

### System cron / Kubernetes CronJobs

Offload scheduling to the platform. Rejected now: system cron on the host
coordinates poorly with an application that owns the state, and CronJobs presume
the Kubernetes backend that the MVP deliberately defers
([ADR-002](ADR-002-provisioner-driver-abstraction.md)). CronJobs become the
natural choice once the Kubernetes provisioner lands.
