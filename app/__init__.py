"""Mini-DBaaS control plane.

The package is layered so that Docker never leaks past the provisioner boundary
(ADR-002) and so that request handlers only ever record *desired state* while a
reconciler makes reality match (ADR-003):

    api/          HTTP surface (thin; validation + authz + delegation)
    services/     use-cases that mutate desired state and enqueue jobs
    lifecycle/    the workers that converge reality: jobs, reconciler, backups...
    provisioner/  the only place that talks to Docker
    models.py     metadata schema (the source of truth)
"""

__version__ = "0.1.0"
