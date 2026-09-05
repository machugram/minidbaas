"""Baseline metadata schema matching ``app.models``.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("max_instances", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("max_total_memory_mb", sa.Integer(), nullable=False, server_default="8192"),
        sa.Column("max_storage_gb", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "principals",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("api_key_hash", sa.String(length=128), nullable=False),
        sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "team_memberships",
        sa.Column("team_id", sa.String(length=32), sa.ForeignKey("teams.id"), primary_key=True),
        sa.Column(
            "principal_id", sa.String(length=32), sa.ForeignKey("principals.id"), primary_key=True
        ),
        sa.Column("role", sa.String(length=16), nullable=False, server_default="member"),
    )
    op.create_table(
        "instances",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("team_id", sa.String(length=32), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("engine", sa.String(length=16), nullable=False, server_default="postgres"),
        sa.Column("image", sa.String(length=128), nullable=False),
        sa.Column("pg_version", sa.String(length=16), nullable=False),
        sa.Column("size", sa.String(length=16), nullable=False),
        sa.Column("host_port", sa.Integer(), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False, server_default="ready"),
        sa.Column("observed_state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("container_id", sa.String(length=64), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("last_error", sa.String(length=512), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("expiry_stopped_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("team_id", "name", name="uq_instance_team_name"),
    )
    op.create_index("ix_instances_team_id", "instances", ["team_id"])
    op.create_table(
        "credentials",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column(
            "instance_id", sa.String(length=32), sa.ForeignKey("instances.id"), nullable=False, unique=True
        ),
        sa.Column("username", sa.String(length=64), nullable=False, server_default="mdbaas_admin"),
        sa.Column("secret_encrypted", sa.LargeBinary(), nullable=False),
        sa.Column("rotated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "db_users",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("instance_id", sa.String(length=32), sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("privileges", sa.String(length=32), nullable=False, server_default="readwrite"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("instance_id", "username", name="uq_dbuser_instance_name"),
    )
    op.create_index("ix_db_users_instance_id", "db_users", ["instance_id"])
    op.create_table(
        "backups",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("instance_id", sa.String(length=32), sa.ForeignKey("instances.id"), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("location", sa.String(length=256), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_backups_instance_id", "backups", ["instance_id"])
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("instance_id", sa.String(length=32), sa.ForeignKey("instances.id"), nullable=True),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("run_after", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_jobs_instance_id", "jobs", ["instance_id"])
    op.create_index("ix_jobs_state", "jobs", ["state"])
    op.create_index("ix_jobs_run_after", "jobs", ["run_after"])
    op.create_table(
        "ports",
        sa.Column("port", sa.Integer(), primary_key=True),
        sa.Column("instance_id", sa.String(length=32), nullable=True),
        sa.Column("allocated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_ports_instance_id", "ports", ["instance_id"])
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("principal_id", sa.String(length=32), nullable=False),
        sa.Column("instance_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=32), primary_key=True),
        sa.Column("actor_id", sa.String(length=32), nullable=True),
        sa.Column("team_id", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=128), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_audit_log_team_id", "audit_log", ["team_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_team_id", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_ports_instance_id", table_name="ports")
    op.drop_table("ports")
    op.drop_index("ix_jobs_run_after", table_name="jobs")
    op.drop_index("ix_jobs_state", table_name="jobs")
    op.drop_index("ix_jobs_instance_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_backups_instance_id", table_name="backups")
    op.drop_table("backups")
    op.drop_index("ix_db_users_instance_id", table_name="db_users")
    op.drop_table("db_users")
    op.drop_table("credentials")
    op.drop_index("ix_instances_team_id", table_name="instances")
    op.drop_table("instances")
    op.drop_table("team_memberships")
    op.drop_table("principals")
    op.drop_table("teams")
