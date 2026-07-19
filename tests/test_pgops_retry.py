"""Bounded retry around the synchronous psql exec used by credential rotation
and DB-user management (moving-parts §F finding 2). These are the only
instance-touching operations that don't go through the job queue — they can't,
since the response carries a one-time secret — so this retry is their only
protection against a transient exec failure."""

from app.provisioner import ExecResult
from app.services import pgops


class _FlakyExec:
    """Fails its first `fail_times` calls, then succeeds."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def exec(self, instance_id, argv, *, user=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            return ExecResult(1, "transient error")
        return ExecResult(0, "OK")


def test_psql_retries_and_recovers(monkeypatch):
    monkeypatch.setattr(pgops, "_RETRY_DELAY_SECONDS", 0)
    flaky = _FlakyExec(fail_times=1)
    result = pgops.psql(flaky, "inst1", "admin", "SELECT 1")
    assert result.ok
    assert flaky.calls == 2


def test_psql_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(pgops, "_RETRY_DELAY_SECONDS", 0)
    flaky = _FlakyExec(fail_times=99)
    result = pgops.psql(flaky, "inst1", "admin", "SELECT 1")
    assert not result.ok
    assert flaky.calls == pgops._RETRY_ATTEMPTS


def test_psql_succeeds_first_try_without_retrying(monkeypatch):
    monkeypatch.setattr(pgops, "_RETRY_DELAY_SECONDS", 0)
    flaky = _FlakyExec(fail_times=0)
    result = pgops.psql(flaky, "inst1", "admin", "SELECT 1")
    assert result.ok
    assert flaky.calls == 1
