from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DEV_LOGIN_ENABLED", "true")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
os.environ.setdefault("ENABLE_BOT", "false")

from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.models import Base  # noqa: E402


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(db: async_sessionmaker) -> AsyncIterator[AsyncSession]:
    async with db() as s:
        yield s


@pytest_asyncio.fixture
async def client(session: AsyncSession, db: async_sessionmaker, monkeypatch) -> AsyncIterator:
    import httpx

    from app import ticker
    from app.db import get_session
    from app.main import app

    async def _override() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = _override
    # Middleware строки событий открывает сессии сам, минуя dependency override.
    monkeypatch.setattr(ticker, "SessionLocal", db)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=True
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
