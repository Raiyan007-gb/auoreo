"""Schema v6 (I2): usage ledger gains cache-write and reasoning tokens (spec §20.2), the
provider generation id (OpenRouter `gen-…`), the serving deployment and request tags (daily
budget ceilings, spec §25.9).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-26

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _set_version(value: str) -> None:
    op.execute("DELETE FROM schema_meta WHERE key = 'schema_version'")
    op.execute(f"INSERT INTO schema_meta (key, value) VALUES ('schema_version', '{value}')")


def upgrade() -> None:
    with op.batch_alter_table("usage") as batch:
        batch.add_column(sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("generation_id", sa.String(128), nullable=True))
        batch.add_column(sa.Column("deployment", sa.String(255), nullable=True))
        batch.add_column(sa.Column("tags", sa.String(512), nullable=True))
    _set_version("6")


def downgrade() -> None:
    with op.batch_alter_table("usage") as batch:
        for col in ("tags", "deployment", "generation_id", "reasoning_tokens", "cache_write_tokens"):
            batch.drop_column(col)
    _set_version("5")
