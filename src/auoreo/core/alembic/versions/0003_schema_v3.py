"""Schema v3 (M5): deliveries, job_runs, teams, team_members, mailbox, kanban.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_version(value: str) -> None:
    op.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    op.execute(f"INSERT INTO schema_meta (key, value) VALUES ('schema_version', '{value}')")


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("external_id", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("target", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(2000), nullable=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_deliveries_dedupe", "deliveries", ["kind", "source", "external_id"], unique=True)
    op.create_index("ix_deliveries_status", "deliveries", ["status", "created_at"])
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job", sa.String(64), nullable=False),
        sa.Column("scheduled_for", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("catch_up", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(2000), nullable=True),
    )
    op.create_index("ix_job_runs_job", "job_runs", ["job"])
    op.create_index("ix_job_runs_dedupe", "job_runs", ["job", "scheduled_for"], unique=True)
    op.create_table(
        "teams",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.String(1000), nullable=False),
        sa.Column("lead_session_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_teams_lead_session_id", "teams", ["lead_session_id"])
    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.String(64), sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(64), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("joined_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
    op.create_index("ix_team_members_name", "team_members", ["team_id", "name"], unique=True)
    op.create_table(
        "mailbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("team_id", sa.String(64), nullable=False),
        sa.Column("recipient", sa.String(64), nullable=False),
        sa.Column("sender", sa.String(64), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("summary", sa.String(200), nullable=False),
        sa.Column("text", sa.String(100000), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("read", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_mailbox_team_id", "mailbox", ["team_id"])
    op.create_index("ix_mailbox_recipient", "mailbox", ["recipient"])
    op.create_table(
        "kanban_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("board", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("body", sa.String(20000), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("worker", sa.String(64), nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_kanban_tasks_board", "kanban_tasks", ["board"])
    op.create_table(
        "kanban_comments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("kanban_tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("author", sa.String(64), nullable=False),
        sa.Column("text", sa.String(20000), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_kanban_comments_task_id", "kanban_comments", ["task_id"])
    _set_version("3")


def downgrade() -> None:
    for table in (
        "kanban_comments",
        "kanban_tasks",
        "mailbox",
        "team_members",
        "teams",
        "job_runs",
        "deliveries",
    ):
        op.drop_table(table)
    _set_version("2")
