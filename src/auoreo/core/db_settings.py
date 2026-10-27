"""Schema v8 tables (revision `0008_settings_providers`): the web-managed settings store and the
provider connections / cached model lists behind Settings → Models.

- `app_settings` — one row per top-level `Settings` key (`value` is its JSON), `updated_by`
  names the writer (`api`, `cli`, `import`, `migration`, …). `app_settings_history` keeps the
  previous full snapshot of every change set (newest 10) for undo; `app_settings_meta.version`
  is bumped by every write so another process can notice a change cheaply (the live watcher).
- `provider_connections` — one connected provider (no key here: keys live in the secret store
  under `secret_name`); `provider_models` — its last fetched model list.

Kept out of `core/db.py` (500-line cap); registered on the same `Base.metadata`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from auoreo.core.db import Base, utcnow


class AppSettingRow(Base):
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_by: Mapped[str] = mapped_column(String(64), default="")


class AppSettingsHistoryRow(Base):
    __tablename__ = "app_settings_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    """The whole `app_settings` content BEFORE the change set."""
    source: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AppSettingsMetaRow(Base):
    __tablename__ = "app_settings_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(4000), default="")


class ProviderConnectionRow(Base):
    __tablename__ = "provider_connections"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    secret_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    api_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="unchecked")  # ok|invalid|error|unchecked
    last_error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    model_count: Mapped[int] = mapped_column(Integer, default=0)
    checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProviderModelRow(Base):
    __tablename__ = "provider_models"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("provider_connections.id", ondelete="CASCADE"))
    model_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), default="")
    kind: Mapped[str] = mapped_column(String(16), default="chat")  # chat|embedding|other
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow)
    __table_args__ = (Index("ix_provider_models_conn", "connection_id", "model_id", unique=True),)


__all__ = [
    "AppSettingRow",
    "AppSettingsHistoryRow",
    "AppSettingsMetaRow",
    "ProviderConnectionRow",
    "ProviderModelRow",
]
