# ADR-004: Patch via image swap on a persistent volume

**Status:** Accepted
**Date:** 2026-07
**Owners:** Platform
**Related:** [ADR-001](ADR-001-control-plane-data-plane-split.md), [ADR-003](ADR-003-desired-state-reconciler.md)

## Status Quo

The JD calls for a "patch this instance" workflow. Patching a database can mean
several things — OS/package updates, Postgres minor upgrades, or major-version
upgrades — and each has a different mechanism and risk profile. We need a
concrete, honest definition of what "patch" does in this platform, because an
under-specified patch button is how you lose data.

## Goal

Provide a reliable "move this instance to a new Postgres version" operation that
preserves all data, has a clear failure/rollback story, and is simple enough to
automate. Minor-version upgrades should be routine and low-risk.

## Non-Goal

We are not building automated **major-version** upgrades (which require
`pg_upgrade` or dump/restore and careful compatibility handling) in the MVP — the
image-swap mechanism handles minor upgrades cleanly and is *allowed* for major
bumps only with an explicit pre-patch backup and user acknowledgement. We are
also not doing zero-downtime patching; a short restart is accepted.

## Proposed Solution

Because tenant data lives in a Docker volume ([ADR-001](ADR-001-control-plane-data-plane-split.md)),
patching is a **container image swap against the same volume**, not a data
migration.

**The mechanism is: stop, replace, restart, verify.** The provisioner's `patch`
verb (from [ADR-002](ADR-002-provisioner-driver-abstraction.md)) recreates the
container from the new image while re-attaching the existing volume, then waits on
the Postgres healthcheck before flipping `observed_state` back to `READY`. For a
minor upgrade (e.g. 16.3 → 16.4) the on-disk data directory format is unchanged,
so this is genuinely just swapping the binaries around the same `PGDATA`.

**A pre-patch backup is the rollback plan.** The workflow optionally (default on)
takes a backup before touching the container, so a failed or regressed patch can
be recovered by restoring into a fresh instance. Combined with the reconciler
([ADR-003](ADR-003-desired-state-reconciler.md)), a patch that fails healthcheck
leaves the instance in `FAILED`, and the operator can retry or roll back rather
than being stuck in a half-upgraded state.

**Major upgrades are gated, not silently allowed.** Detecting a major-version
delta, the API requires an explicit acknowledgement flag and always forces the
pre-patch backup, because the data directory *is* incompatible across majors and
image-swap alone would fail to start. Full automated major upgrades
(`pg_upgrade`) are a roadmap item.

## Alternatives

### In-place `apt`/`yum` upgrade inside the running container

Patch the running container's packages without recreating it. Rejected:
containers are meant to be immutable and cattle, not pets; an in-place mutation is
not reproducible, leaves the container diverged from its image, and is lost the
next time the reconciler recreates it.

### Always dump-and-restore into a fresh instance

Treat every patch as a logical migration. Rejected as the default: it is far
slower, takes a longer write-lock/outage, and is unnecessary for minor upgrades
where the data directory is compatible. It remains the correct tool for major
upgrades, which is why we keep it available for that gated path.
