# ADR-002: Provisioner driver abstraction

**Status:** Accepted
**Date:** 2026-07
**Owners:** Platform
**Related:** [ADR-001](ADR-001-control-plane-data-plane-split.md)

## Status Quo

The MVP provisions databases as Docker containers on a single host. The obvious
implementation is to sprinkle `docker` SDK calls directly through the API
handlers and lifecycle jobs wherever a database needs to be created, destroyed,
or inspected. Nothing is built yet, so no such coupling exists — but it is the
default a codebase drifts into without a deliberate boundary.

## Goal

Confine every piece of Docker-specific knowledge behind one narrow interface so
that (a) the rest of the system reasons about "instances," not "containers," and
(b) a future Kubernetes backend can be added as a new implementation rather than
a rewrite. No control-plane logic outside the driver should import the Docker SDK.

## Non-Goal

We are not building the Kubernetes driver now, and we are not designing a
lowest-common-denominator interface that pre-emptively supports every possible
backend. The Protocol is shaped by the two backends we actually care about
(Docker now, Kubernetes later); other backends can extend it when they arrive.

## Proposed Solution

We define a `Provisioner` Protocol and program every caller against it.

```python
class Provisioner(Protocol):
    def create(spec: InstanceSpec) -> RuntimeHandle: ...
    def destroy(handle: RuntimeHandle) -> None: ...
    def resize(handle: RuntimeHandle, new_spec: InstanceSpec) -> RuntimeHandle: ...
    def patch(handle: RuntimeHandle, new_image: str) -> RuntimeHandle: ...
    def status(handle: RuntimeHandle) -> RuntimeStatus: ...
```

**The interface speaks in domain nouns, not Docker nouns.** `InstanceSpec`
carries size, image, and limits; `RuntimeHandle` is an opaque reference the
driver knows how to resolve (a container id today, a StatefulSet name tomorrow);
`RuntimeStatus` is a normalized health/phase enum. Callers never see a container.

**`DockerProvisioner` is the only implementation for the MVP**, and it is the
*only* module allowed to import the Docker SDK. It maps sizes to `mem_limit` /
`nano_cpus`, manages the volume, applies the `mdbaas.*` labels, and binds the
allocated port. Because the abstraction is small and the Kubernetes primitives
(StatefulSet + PVC + Service) map cleanly onto the same five verbs, the later
`KubernetesProvisioner` is additive — a dependency-injection swap, guarded by the
same tests written against the Protocol.

**This abstraction is what makes the k8s roadmap credible.** The
[Design Review](../design-review.md) lists Kubernetes as a v2 item precisely
because this boundary means it doesn't touch the API, the metadata schema, or the
reconciler's logic — only a new class behind the same seam.

## Alternatives

### Direct Docker SDK calls throughout the codebase

Less indirection, faster to write the first version. Rejected because it would
scatter backend assumptions (container ids, exec semantics, volume mounts) across
handlers and jobs, making the eventual Kubernetes port a cross-cutting rewrite
and making the code untestable without a real Docker daemon.

### Adopt an existing operator/framework (e.g. a Kubernetes operator now)

Skip the Docker phase and build straight on a Postgres operator. Rejected for the
MVP: it forces a Kubernetes dependency on day one (heavier local setup, slower
demo loop) and buys capability we don't yet need. The Protocol keeps that door
open without paying for it upfront.
