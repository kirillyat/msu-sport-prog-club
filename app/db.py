from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False, future=True)


def _unicode_lower(value):
    return value.lower() if isinstance(value, str) else value


def configure_connection(dbapi_connection, _record=None) -> None:
    """Настройка соединения. Общая для приложения и тестов.

    WAL даёт параллельное чтение во время записи — синкер и веб живут
    в одном процессе.

    Встроенный lower() в SQLite умеет только латиницу: lower('Кирилл')
    возвращает 'Кирилл'. Из-за этого регистронезависимые сравнения имён
    молча не работали. Подменяем его питоновским — в Postgres lower()
    и так юникодный, так что запросы остаются переносимыми.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()
    dbapi_connection.create_function("lower", 1, _unicode_lower, deterministic=True)


event.listen(engine.sync_engine, "connect", configure_connection)


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
