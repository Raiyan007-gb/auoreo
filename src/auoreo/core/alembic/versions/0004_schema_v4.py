"""Schema v4 (M3–M5 gaps): goals, suggestions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_version(value: str) -> None:
    op.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    op.execute(f"INSERT INTO schema_meta (key, value) VALUES ('schema_version', '{value}')")


def upgrade() -> None:
    op.create_table(
        "goals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.String(5000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("evidence_session_ids", sa.JSON(), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("absorbed_into", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_goals_status", "goals", ["status"])
    op.create_table(
        "suggestions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.String(4000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("deliver", sa.String(64), nullable=False),
        sa.Column("origin_session", sa.String(64), nullable=True),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=True),
        sa.Column("delivery_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_suggestions_status", "suggestions", ["status", "created_at"])
    op.create_index("ix_suggestions_dedupe", "suggestions", ["dedupe_key"])
    _set_version("4")


def downgrade() -> None:
    op.drop_table("suggestions")
    op.drop_table("goals")
    _set_version("3")
