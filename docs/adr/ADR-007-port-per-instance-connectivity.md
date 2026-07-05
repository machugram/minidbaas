# ADR-007: Port-per-instance connectivity for the MVP

**Status:** Accepted
**Date:** 2026-07
**Owners:** Platform

## Status Quo

Clients need to actually connect to the databases the platform provisions. On a
single Docker host, each Postgres container listens on 5432 *inside* its network
namespace, so the host must expose each one somehow. The two shapes are: publish
a distinct host port per container, or front all instances with a shared proxy
that routes by hostname/identity. Nothing is built, so the connection story is
open.

## Goal

Give every provisioned instance a working, addressable connection endpoint with
the least machinery, so the end-to-end demo (create → get DSN → `psql`) works on a
laptop.

## Non-Goal

This decision is explicitly *not* the long-term connectivity model. It does not
provide TLS, does not scale beyond one host, and does not solve connection
pooling or public exposure. Those are owned by the future shared-proxy work.

## Proposed Solution

For the MVP each instance is published on a **dedicated host port bound to
`127.0.0.1`**, drawn from a configured range (e.g. 15000–16000) and recorded in
the metadata DB.

**Port allocation is done in the database, atomically, to avoid a race.** As the
[Design Review §2.1](../design-review.md#correctness--concurrency) warns, "find a
free port in memory then bind it" is a TOCTOU bug — two concurrent creates can
choose the same port. Instead a `ports` table holds the range and allocation is
an atomic `UPDATE ... WHERE instance_id IS NULL RETURNING port`; the container is
never bound until that row commits. This keeps the simple model correct under
concurrency.

**Binding to localhost is a deliberate safety default.** Instances are reachable
from the host (and via SSH/VPN tunnels) but are not published on `0.0.0.0`, so a
fresh install doesn't accidentally expose unauthenticated Postgres to the
network. The emitted DSN reflects the host/port pair.

**We accept that this does not scale and say so.** Port ranges are finite, there
is no TLS on the wire, and it is single-host only. The designated replacement — a
shared **pgbouncer / SNI-routing proxy** that terminates TLS and routes to
instances by name — is on the roadmap; isolating connectivity behind the DSN the
platform returns means clients don't hard-code assumptions that the proxy would
later break.

## Alternatives

### Shared pgbouncer / SNI proxy from day one

The correct long-term model: one ingress, TLS termination, no port exhaustion,
name-based routing. Rejected for the MVP because it adds a routing component and
per-instance certificate/hostname management before the core provisioning story
is even proven — more moving parts than a laptop demo warrants. It is the planned
successor precisely because the port-per-instance approach is known not to scale.

### Publish on `0.0.0.0` (all interfaces)

Marginally simpler for remote access. Rejected: it exposes tenant databases to
the host's network by default, a serious security regression for zero real
benefit given SSH/VPN tunneling covers legitimate remote use in the MVP.
