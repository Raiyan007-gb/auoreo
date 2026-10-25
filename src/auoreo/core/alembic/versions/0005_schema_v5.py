"""Schema v5 (M6): blackboard artifacts, bandit policy/outcomes, usage latency + outcome,
append-only audit triggers.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit "
    "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit "
    "BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END",
)


def _set_version(value: str) -> None:
    op.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    op.execute(f"INSERT INTO schema_meta (key, value) VALUES ('schema_version', '{value}')")


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value", sa.String(65_536), nullable=False),
        sa.Column("producer", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_artifacts_key", "artifacts", ["team_id", "key"], unique=True)
    op.create_table(
        "bandit_policy",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("context_key", sa.String(128), nullable=False),
        sa.Column("arm_key", sa.String(255), nullable=False),
        sa.Column("arm", sa.JSON(), nullable=False),
        sa.Column("pulls", sa.Integer(), nullable=False),
        sa.Column("reward_sum", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bandit_policy_arm", "bandit_policy", ["context_key", "arm_key"], unique=True)
    op.create_table(
        "bandit_outcomes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("context_key", sa.String(128), nullable=False),
        sa.Column("arm_key", sa.String(255), nullable=False),
        sa.Column("reward", sa.Float(), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bandit_outcomes_context", "bandit_outcomes", ["context_key", "created_at"])
    with op.batch_alter_table("usage") as batch:
        batch.add_column(sa.Column("latency_ms", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("outcome", sa.String(32), nullable=True))
    for sql in AUDIT_TRIGGERS:
        op.execute(sql)
    _set_version("5")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_no_update")
    op.execute("DROP TRIGGER IF EXISTS audit_no_delete")
    with op.batch_alter_table("usage") as batch:
        batch.drop_column("outcome")
        batch.drop_column("latency_ms")
    op.drop_table("bandit_outcomes")
    op.drop_table("bandit_policy")
    op.drop_table("artifacts")
    _set_version("4")
