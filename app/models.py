"""SQLAlchemy 2.0 async модели для хранения звонков и перезвонов."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Call(Base):
    """Каждое событие call_saved от Utel.uz."""

    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Уникальный ID из Utel — используется для дедупликации (заменяет in-memory dict)
    call_id: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)

    direction: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)   # 'in' / 'out'
    external_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    operator_ext: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # внутренний номер/ext
    operator_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    call_time_utc: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    wait_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    answered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    # Связь с missed_tracking (может не быть — только для пропущенных входящих)
    missed_tracking: Mapped[Optional[MissedTracking]] = relationship(
        "MissedTracking", back_populates="call", uselist=False
    )


class MissedTracking(Base):
    """Отслеживание статуса перезвона по пропущенному входящему звонку."""

    __tablename__ = "missed_tracking"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    call_id_fk: Mapped[Optional[str]] = mapped_column(
        ForeignKey("calls.id", ondelete="SET NULL"), nullable=True, index=True
    )
    external_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, index=True)
    operator_ext: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    missed_at_utc: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # Перезвон автоматически (матчинг по исходящему вебхуку)
    called_back: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    called_back_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    called_back_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Перезвон вручную (нажатие кнопки в Telegram)
    manual: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Эскалация — напоминание если не перезвонили
    escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    tg_message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    call: Mapped[Optional[Call]] = relationship("Call", back_populates="missed_tracking")


class BotUser(Base):
    """Пользователи бота с ролями (RBAC)."""

    __tablename__ = "bot_users"

    tg_user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # 'pending' | 'seller' | 'manager' | 'rejected'
    role: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    # Для роли seller: привязка к оператору Utel и пользователю AmoCRM
    utel_ext: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    amocrm_user_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    approved_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, onupdate=func.now(), nullable=True)


class AmocrmToken(Base):
    """Одна строка — токены AmoCRM (один аккаунт)."""

    __tablename__ = "amocrm_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    subdomain: Mapped[str] = mapped_column(String(128), nullable=False)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class HermesConversation(Base):
    __tablename__ = "hermes_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tg_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # "user" или "assistant"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class HermesAuditCache(Base):
    __tablename__ = "hermes_audit_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    amocrm_user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    lead_id: Mapped[int] = mapped_column(Integer, nullable=False)
    heat: Mapped[str] = mapped_column(String(8), nullable=False)  # "hot"|"warm"|"cold"
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    raw_analysis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


_hermes_audit_idx = Index(
    "ix_hermes_audit_user_lead",
    HermesAuditCache.amocrm_user_id,
    HermesAuditCache.lead_id,
)
