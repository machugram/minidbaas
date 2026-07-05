# ADR-003: Desired-state model with a reconciler

**Status:** Accepted
**Date:** 2026-07
**Owners:** Platform
**Related:** [ADR-001](ADR-001-control-plane-data-plane-split.md)

## Status Quo

An API handler that receives "create a database" could simply do it inline: call
Docker, wait for the container, write the result, and return. This imperative
"do it now" style is the default, and for a single happy-path request it works.
But provisioning takes seconds, can fail partway, and can race with other
operations on the same instance — and an inline approach has nowhere to recover
from a crash that happens between "container started" and "row written."

## Goal

Make the system converge on the operator's intent regardless of transient
failures or restarts. An API call should record *what should be true* and return
quickly; a separate reconciler should be responsible for *making it true* and for
continuously correcting drift between recorded intent and observed reality.

## Non-Goal

This is not a general workflow engine or saga framework. We are not modeling
arbitrary multi-resource transactions — each instance reconciles independently.
Distributed scheduling across multiple hosts is out of scope (single host in the
MVP; see [ADR-006](ADR-006-in-process-scheduler.md) for the scheduler's limits).

## Proposed Solution

Every instance row carries a **`desired_state`** and an **`observed_state`**. API
handlers only ever write desired state (and enqueue a `jobs` row); a reconciler
drives observed toward desired.

**Writes are fast and crash-safe.** `POST /instances` validates, writes
`desired=READY` + a `PROVISION` job inside one transaction, and returns `202`.
If the process dies immediately after, the intent survives in the DB and the
reconciler picks it up on the next tick — there is no lost work and no orphaned
half-created container that nobody remembers.

**The reconciler is level-triggered, not edge-triggered.** It periodically
compares desired vs. observed for every non-terminal instance and takes the
action that closes the gap: `desired=READY, observed=absent` ⇒ create;
`desired=DELETED, observed=present` ⇒ destroy; `observed=crashed` ⇒ restart.
Because it reacts to *state*, not to *events*, a missed or duplicated event
cannot corrupt it — the next pass simply re-derives the correct action.

**Reconciling on desired state is also the fix for the delete-race.** As the
[Design Review §2.2](../design-review.md#2-correctness--concurrency) notes, a
reconciler that recreated any "missing" container could resurrect a database the
user just deleted. Keying strictly on `desired_state`, and using the `jobs` row
as a per-instance lock so exactly one actor mutates an instance at a time,
eliminates that class of bug. Every provisioner action is idempotent and keyed by
instance id, so re-running a partially-applied action is safe.

**Ground truth comes from two durable sources:** the metadata DB (intent) and the
`mdbaas.*` labels on containers (reality). The reconciler needs nothing in memory
to rebuild its worldview after a restart, which is what lets the control plane be
freely redeployed (per [ADR-001](ADR-001-control-plane-data-plane-split.md)).

## Alternatives

### Imperative, synchronous provisioning in the request handler

Do the Docker work inline and return when done. Rejected: unbounded request
latency, no recovery from mid-operation crashes, and no mechanism to correct
drift (a container that dies later stays dead until a human notices).

### Event-sourced / edge-triggered orchestration

Drive state transitions purely from an event stream. Rejected as overkill and
more fragile here: it requires exactly-once event handling to stay correct,
whereas a level-triggered reconciler is self-correcting under lost or duplicated
signals and is far simpler to reason about on a single host.
