"""Use-case layer: enforce policy, mutate *desired* state, enqueue jobs.

Services never talk to Docker directly and never block on provisioning — they
write intent and hand off to the lifecycle workers (ADR-003).
"""
