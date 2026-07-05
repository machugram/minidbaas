"""The workers that converge reality toward desired state (ADR-003):

    jobs         durable queue: enqueue / claim / retry / dead-letter (DR-3)
    actions      idempotent handlers for each job type
    backups      pg_dump / prune / restore
    reaper       two-phase TTL expiry + orphan enqueue (DR-7)
    reconciler   drift detection + orphan GC (DR-2)
    scheduler    APScheduler wiring, each tick advisory-locked (DR-4)
"""
