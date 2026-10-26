"""Schema v7 (I3): team member metadata, usage attribution, kanban task profile.

`team_members` gains `subagent_id` (`sa-{n}-{hex8}`), `agent_type`, `model`, `color`, `cwd`
(spec §16.4); `usage` gains `parent_session_id`, `subagent_id`, `depth`, `task_name`
(spec §25.15 cost attribution); `kanban_tasks` gains `profile` (worker agent, spec §16.6).

Every column is nullable and added only when missing, so this revision is independent of
any other v6 revision and can be re-pointed (`down_revision`) when branches merge.

Revision ID: 0007
Revises: 0005
Create Date: 2026-10-26

"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns() -> dict[str, list[sa.Column[Any]]]:
    return {
        "team_members": [
            sa.Column("subagent_id", sa.String(32), nullable=True),
            sa.Column("agent_type", sa.String(64), nullable=True),
            sa.Column("model", sa.String(255), nullable=True),
            sa.Column("color", sa.String(16), nullable=True),
            sa.Column("cwd", sa.String(1024), nullable=True),
        ],
        "usage": [
            sa.Column("parent_session_id", sa.String(64), nullable=True),
            sa.Column("subagent_id", sa.String(32), nullable=True),
            sa.Column("depth", sa.Integer(), nullable=True),
            sa.Column("task_name", sa.String(64), nullable=True),
        ],
        "kanban_tasks": [sa.Column("profile", sa.String(64), nullable=True)],
    }


def _set_version(value: str) -> None:
    op.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    op.execute(f"INSERT INTO schema_meta (key, value) VALUES ('schema_version', '{value}')")


def _existing(table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table, cols in _columns().items():
        have = _existing(table)
        missing = [c for c in cols if c.name not in have]
        if missing:
            with op.batch_alter_table(table) as batch:
                for col in missing:
                    batch.add_column(col)
    indexes = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("usage")}
    if "ix_usage_subagent_id" not in indexes:
        op.create_index("ix_usage_subagent_id", "usage", ["subagent_id"])
    _set_version("7")


def downgrade() -> None:
    op.drop_index("ix_usage_subagent_id", table_name="usage")
    for table, cols in _columns().items():
        with op.batch_alter_table(table) as batch:
            for col in reversed(cols):
                batch.drop_column(col.name)
    _set_version("5")
