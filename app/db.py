"""Асинхронный движок SQLAlchemy + сессия + init_db."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base

logger = logging.getLogger(__name__)

# SQLite-файл лежит в ./data/calls.db относительно CWD (создаётся при старте).
# Для будущего Cloudflare D1 — тот же SQLite-диалект, схема переносится без изменений.
_DEFAULT_DB_URL = "sqlite+aiosqlite:///./data/calls.db"

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_db_url() -> str:
    url = os.environ.get("DATABASE_URL", _DEFAULT_DB_URL)
    # Railway даёт postgresql://, SQLAlchemy async требует postgresql+asyncpg://
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _build_engine():
    global _engine, _session_factory
    url = _get_db_url()
    is_sqlite = url.startswith("sqlite")
    _engine = create_async_engine(
        url,
        echo=False,
        connect_args={"check_same_thread": False} if is_sqlite else {},
    )
    _session_factory = async_sessionmaker(
        _engine, expire_on_commit=False, class_=AsyncSession
    )
    logger.info("Database engine created: %s", url.split("///")[0])


async def init_db() -> None:
    """Создаёт таблицы (если не существуют) и инициализирует директорию data/."""
    # Убедимся что директория data/ существует
    db_url = _get_db_url()
    if "sqlite" in db_url and "///" in db_url:
        db_path = db_url.split("///")[1]
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)

    _build_engine()

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    logger.info("Database tables created/verified.")


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Контекстный менеджер сессии. Использовать: async with get_session() as s: ..."""
    if _session_factory is None:
        raise RuntimeError("DB not initialized. Call init_db() first.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """Закрыть пул соединений при остановке приложения."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("Database engine disposed.")
