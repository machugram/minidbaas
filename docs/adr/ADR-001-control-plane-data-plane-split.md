# ADR-001: Control-plane / data-plane split

**Status:** Accepted
**Date:** 2026-07
**Owners:** Platform

## Status Quo

The naïve way to build a "database-as-a-service" is a single process that both
serves the API and *is* the thing managing databases, holding container handles
in memory and doing work synchronously inside request handlers. Nothing exists
yet, so we are free to choose — but that naïve shape couples the availability of
the databases to the availability of the API, and loses all state on restart.

## Goal

Establish two clearly separated planes: a **control plane** (the FastAPI service,
its metadata database, and background workers) that decides and records *what
should exist*, and a **data plane** (the tenant Postgres containers) that holds
customer data and serves connections. Tenant databases must keep serving traffic
even when the control plane is down or being redeployed.

## Non-Goal

This ADR does not choose the provisioning technology (Docker vs. Kubernetes —
see [ADR-002](ADR-002-provisioner-driver-abstraction.md)) nor the mechanism that
makes reality match desired state (see [ADR-003](ADR-003-desired-state-reconciler.md)).
It also does not address high availability *of the control plane itself*; the
metadata DB remains a single point of failure for new operations in the MVP.

## Proposed Solution

We split the system into two planes with a hard boundary between them.

**The control plane owns intent, never live data.** The API validates requests,
enforces tenancy and quotas, writes desired + observed state to a metadata
Postgres it runs for itself, and enqueues jobs. It never stores tenant table
data and never holds irreplaceable in-memory state — everything needed to
reconstruct the world lives in the metadata DB and in the Docker labels on
managed containers.

**The data plane is disposable infrastructure around durable volumes.** Each
tenant database is a container whose state lives in a Docker volume. The control
plane treats containers as cattle: it can destroy and recreate one from
`(image, volume, config)` without data loss. Crucially, those containers have no
runtime dependency on the control plane — once running, a tenant DB serves
`psql` connections whether or not the API is up.

**This decoupling is the platform's core reliability property.** A control-plane
deploy, crash, or metadata-DB outage degrades *management* operations
(create/delete/patch) but not *data* operations (existing apps keep querying
their databases). Stating this as a designed guarantee — not an accident — shapes
every downstream choice: workers must be restart-safe, and the reconciler must be
able to rebuild its view purely from persisted state + labels.

## Alternatives

### Single monolithic process holding state in memory

Simplest to write first. Rejected because an API restart would orphan every
container it was tracking, synchronous Docker calls would make request latency
unbounded, and any crash mid-operation would leave undiscoverable partial state.

### Storing tenant data in the control-plane database (schema-per-tenant)

Instead of separate containers, give each tenant a schema in one big shared
Postgres. Rejected: it defeats the product's purpose (real isolated instances
with their own versions, resource limits, and superuser access), couples all
tenants' fate together, and makes per-instance patching/resizing impossible.
