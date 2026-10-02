# Mini-DBaaS — Documentation

Design and planning docs for the Mini-DBaaS self-service platform. Start with the
architecture, then the requirements, then the decisions and their scrutiny.

## Index

| Doc | What it is |
|---|---|
| [../ARCHITECTURE.md](../ARCHITECTURE.md) | System architecture & phased build plan |
| [BRD.md](BRD.md) | Business Requirements — drivers, objectives, scope, business rules |
| [PRD.md](PRD.md) | Product Requirements — personas, user stories, FRs/NFRs, acceptance criteria |
| [diagrams.md](diagrams.md) | Mermaid diagrams: context, components, sequences, state machine, ER model |
| [design-review.md](design-review.md) | Adversarial review of the design + prioritized improvement roadmap |
| [moving-parts-model.md](moving-parts-model.md) | Deterministic, code-verified model: dependency graph, per-operation sequence diagrams, exact state-transition sites, ER diagram from the ORM, scheduler tick map |
| [system-design-walkthrough.md](system-design-walkthrough.md) | Interview-style narrative explanation — requirements → design → deep dives → trade-offs — for onboarding or interview prep |

## Architecture Decision Records

Prometheus-style ADRs (Status Quo → Goal → Non-Goal → Proposed Solution →
Alternatives). Each records one contested decision and the roads not taken.

| ADR | Decision |
|---|---|
| [ADR-001](adr/ADR-001-control-plane-data-plane-split.md) | Control-plane / data-plane split |
| [ADR-002](adr/ADR-002-provisioner-driver-abstraction.md) | Provisioner driver abstraction (Docker now, k8s-ready) |
| [ADR-003](adr/ADR-003-desired-state-reconciler.md) | Desired-state model with a reconciler |
| [ADR-004](adr/ADR-004-patch-via-image-swap.md) | Patch via image swap on a persistent volume |
| [ADR-005](adr/ADR-005-secrets-encrypted-in-metadata-db.md) | Credentials encrypted at rest; API keys hashed |
| [ADR-006](adr/ADR-006-in-process-scheduler.md) | In-process scheduler for lifecycle automation |
| [ADR-007](adr/ADR-007-port-per-instance-connectivity.md) | Port-per-instance connectivity for the MVP |

## How the docs relate

- **BRD** says *why the business wants this*; **PRD** says *what the product must
  do*; **ARCHITECTURE** says *how it's built*; **ADRs** justify *specific
  choices*; **design-review** stress-tests all of it and sets the improvement
  order; **moving-parts-model** shows *what the code actually does*, verified
  line-by-line, as a check against everything else drifting from reality;
  **system-design-walkthrough** ties all of the above into one narrative,
  cross-linking rather than repeating — read that one first if you're new.
- The design review's roadmap (§7) is the backlog: items 1–8 are folded into the
  MVP, 9–15 are fast-follows (9–11 are ✅ fixed post-MVP findings), 16+ are v2.
