"""Schema v8: settings in state.db + provider connections (Settings → Models).

`app_settings` (one row per top-level settings key, JSON value), `app_settings_history` (the
previous snapshot of each change set, newest 10 kept by the writer), `app_settings_meta`
(`version` counter the live watcher polls), `provider_connections` and `provider_models`.
Provider keys are never stored here (they live in the secret store). Tables are created only
when missing, so the revision is idempotent on a database built by `create_all`.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_version(value: str) -> None:
    op.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    op.execute(f"INSERT INTO schema_meta (key, value) VALUES ('schema_version', '{value}')")


def upgrade() -> None:
    have = set(sa.inspect(op.get_bind()).get_table_names())
    if "app_settings" not in have:
        op.create_table(
            "app_settings",
            sa.Column("key", sa.String(128), primary_key=True),
            sa.Column("value", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("updated_by", sa.String(64), nullable=False, server_default=""),
        )
    if "app_settings_history" not in have:
        op.create_table(
            "app_settings_history",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("snapshot", sa.JSON(), nullable=False),
            sa.Column("source", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if "app_settings_meta" not in have:
        op.create_table(
            "app_settings_meta",
            sa.Column("key", sa.String(64), primary_key=True),
            sa.Column("value", sa.String(4000), nullable=False, server_default=""),
        )
    if "provider_connections" not in have:
        op.create_table(
            "provider_connections",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("provider", sa.String(64), nullable=False),
            sa.Column("label", sa.String(128), nullable=False, server_default=""),
            sa.Column("secret_name", sa.String(255), nullable=True),
            sa.Column("base_url", sa.String(1024), nullable=True),
            sa.Column("api_version", sa.String(64), nullable=True),
            sa.Column("status", sa.String(16), nullable=False, server_default="unchecked"),
            sa.Column("last_error", sa.String(2000), nullable=True),
            sa.Column("model_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("checked_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_provider_connections_provider", "provider_connections", ["provider"])
    if "provider_models" not in have:
        op.create_table(
            "provider_models",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "connection_id",
                sa.Integer(),
                sa.ForeignKey("provider_connections.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("model_id", sa.String(255), nullable=False),
            sa.Column("name", sa.String(255), nullable=False, server_default=""),
            sa.Column("kind", sa.String(16), nullable=False, server_default="chat"),
            sa.Column("context_window", sa.Integer(), nullable=True),
            sa.Column("input_price", sa.Float(), nullable=True),
            sa.Column("output_price", sa.Float(), nullable=True),
            sa.Column("meta", sa.JSON(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_provider_models_conn", "provider_models", ["connection_id", "model_id"], unique=True
        )
    _set_version("8")


def downgrade() -> None:
    op.drop_index("ix_provider_models_conn", table_name="provider_models")
    op.drop_table("provider_models")
    op.drop_index("ix_provider_connections_provider", table_name="provider_connections")
    for table in ("provider_connections", "app_settings_meta", "app_settings_history", "app_settings"):
        op.drop_table(table)
    _set_version("7")
