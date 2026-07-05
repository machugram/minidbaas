"""In-process lifecycle scheduler (ADR-006).

APScheduler ticks four jobs: drain the work queue, reconcile drift, sweep expiry,
and enqueue due backups. Each *maintenance* tick is wrapped in a Postgres advisory
lock (DR-4) so that running more than one API replica can't double-fire backups or
reconcile concurrently. The worker drain needs no lock — ``claim_next`` already
uses ``SKIP LOCKED`` so parallel drains are safe.

Known limitation (stated, not hidden): if this single process is down, ticks
simply don't fire during that window (ADR-006). The durable-worker upgrade path is
on the roadmap.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import get_settings
from app.db import engine, session_scope
from app.lifecycle import backups, jobs, reaper, reconciler, worker
from app.models import BackupKind, JobType
from app.provisioner import Provisioner

log = logging.getLogger(__name__)

# Arbitrary, stable keys — one per lockable tick.
_LOCK_RECONCILE = 4711001
_LOCK_REAPER = 4711002
_LOCK_BACKUPS = 4711003


def _is_postgres() -> bool:
    return engine.url.get_backend_name().startswith("postgresql")


@contextmanager
def _advisory_lock(key: int):
    """Yields True if this process holds the lock for the tick. No-op (always True)
    on non-Postgres backends used by the tests."""
    if not _is_postgres():
        yield True
        return
    conn = engine.connect()
    got = bool(conn.exec_driver_sql("SELECT pg_try_advisory_lock(%s)", (key,)).scalar())
    try:
        yield got
    finally:
        if got:
            conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (key,))
        conn.close()


def _tick_worker(provisioner: Provisioner) -> None:
    try:
        worker.drain(provisioner)
    except Exception:  # noqa: BLE001
        log.exception("worker tick failed")


def _tick_reconcile(provisioner: Provisioner) -> None:
    with _advisory_lock(_LOCK_RECONCILE) as held:
        if not held:
            return
        try:
            with session_scope() as session:
                reconciler.reconcile(session, provisioner)
        except Exception:  # noqa: BLE001
            log.exception("reconcile tick failed")


def _tick_reaper(provisioner: Provisioner) -> None:
    with _advisory_lock(_LOCK_REAPER) as held:
        if not held:
            return
        try:
            with session_scope() as session:
                reaper.sweep(session)
        except Exception:  # noqa: BLE001
            log.exception("reaper tick failed")


def _tick_backups(provisioner: Provisioner) -> None:
    with _advisory_lock(_LOCK_BACKUPS) as held:
        if not held:
            return
        try:
            with session_scope() as session:
                for instance in backups.due_for_backup(session):
                    jobs.enqueue_once(session, JobType.BACKUP, instance.id, {"kind": BackupKind.SCHEDULED.value})
                backups.prune_old(session, provisioner)
        except Exception:  # noqa: BLE001
            log.exception("backup tick failed")


class LifecycleScheduler:
    def __init__(self, provisioner: Provisioner):
        self._provisioner = provisioner
        self._scheduler = BackgroundScheduler()

    def start(self) -> None:
        s = get_settings()
        p = [self._provisioner]
        common = {"max_instances": 1, "coalesce": True}
        self._scheduler.add_job(_tick_worker, "interval", seconds=s.worker_interval_seconds, args=p, id="worker", **common)
        self._scheduler.add_job(_tick_reconcile, "interval", seconds=s.reconcile_interval_seconds, args=p, id="reconcile", **common)
        self._scheduler.add_job(_tick_reaper, "interval", seconds=s.reaper_interval_seconds, args=p, id="reaper", **common)
        self._scheduler.add_job(_tick_backups, "cron", hour=s.backup_cron_hour, args=p, id="backups", **common)
        self._scheduler.start()
        log.info("lifecycle scheduler started")

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
