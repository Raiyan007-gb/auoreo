"""Baseline: the state.db schema exactly as M2 shipped it (schema_version 1).

Revision ID: 0001
Revises:
Create Date: 2026-10-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schema_meta",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(255), nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("workflow", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parent_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "events",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("aggregate_seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_events_agg", "events", ["aggregate_id", "aggregate_seq"], unique=True)
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id", sa.String(64), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("message", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_messages_session", "messages", ["session_id", "idx"], unique=True)
    op.create_table(
        "audit",
        sa.Column("seq", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("row_hash", sa.String(64), nullable=False),
    )
    op.create_table(
        "idempotency",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.execute("INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1')")


def downgrade() -> None:
    for table in ("idempotency", "audit", "messages", "events", "sessions", "schema_meta"):
        op.drop_table(table)
