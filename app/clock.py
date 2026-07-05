"""Single source of "now".

All persisted timestamps are naive UTC. Rationale: ``Mapped[datetime]`` maps to
``TIMESTAMP WITHOUT TIME ZONE`` on Postgres and to a tz-less string on SQLite, so
values always read back naive. Standardising on naive-UTC everywhere avoids the
"can't compare offset-naive and offset-aware datetimes" class of bug, while
``to_naive_utc`` normalises any aware value a client sends in (e.g. ``expires_at``).
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt
