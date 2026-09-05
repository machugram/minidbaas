"""Alembic script template (unused at runtime; kept for ``alembic revision``)."""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str | None = ${repr(up_revision)}
down_revision: str | Sequence[str] | None = ${repr(down_revision)}
branch_labels: str | Sequence[str] | None = ${repr(branch_labels)}
depends_on: str | Sequence[str] | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
