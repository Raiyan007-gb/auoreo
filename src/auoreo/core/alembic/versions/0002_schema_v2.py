"""Schema v2 (M3): sessions.archived, usage ledger, messages_archive.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_version(value: str) -> None:
    op.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    op.execute(f"INSERT INTO schema_meta (key, value) VALUES ('schema_version', '{value}')")


def upgrade() -> None:
    with op.batch_alter_table("sessions") as batch:
        batch.add_column(sa.Column("archived", sa.Boolean(), nullable=False, server_default="0"))
    op.create_table(
        "usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("estimated", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_usage_session_id", "usage", ["session_id"])
    op.create_table(
        "messages_archive",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id", sa.String(64), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("compaction_id", sa.String(64), nullable=False),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("message", sa.JSON(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_messages_archive_session", "messages_archive", ["session_id", "compaction_id"])
    _set_version("2")


def downgrade() -> None:
    op.drop_index("ix_messages_archive_session", "messages_archive")
    op.drop_table("messages_archive")
    op.drop_index("ix_usage_session_id", "usage")
    op.drop_table("usage")
    with op.batch_alter_table("sessions") as batch:
        batch.drop_column("archived")
    _set_version("1")
