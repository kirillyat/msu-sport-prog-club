"""Мелкие служебные команды: python -m app.cli <команда>."""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select

from app.db import SessionLocal, engine
from app.models import Base, Platform, Role, User
from app.services.catalog import sync_catalog
from app.services.sync import sync_all

USAGE = """Команды:
  gen-secret           сгенерировать SECRET_KEY для .env
  backup ФАЙЛ          консистентная копия базы (безопасно на работающем сервисе)
  init-db              создать таблицы напрямую (для тестов; в проде — alembic upgrade head)
  sync-catalog         скачать каталоги задач Codeforces и LeetCode
  sync-submissions     обновить посылки всех подтверждённых аккаунтов
  make-teacher NAME    выдать роль преподавателя пользователю с таким именем
  stats                короткая сводка по базе
"""


def gen_secret() -> None:
    import secrets

    print(f"SECRET_KEY={secrets.token_urlsafe(48)}")


def backup(destination: str) -> None:
    """Копия через backup API SQLite.

    Простой `cp` файла при включённом WAL может дать битую копию: часть
    транзакций лежит в -wal и в основной файл ещё не перенесена.
    """
    import sqlite3
    from pathlib import Path

    from app.config import settings

    source_path = settings.data_dir.resolve() / "sport.db"
    if not source_path.exists():
        print(f"базы нет: {source_path}")
        return

    target = Path(destination).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        with sqlite3.connect(target) as destination_db:
            source.backup(destination_db)
    finally:
        source.close()
    print(f"копия готова: {target} ({target.stat().st_size} байт)")


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("таблицы созданы")


async def cmd_sync_catalog() -> None:
    async with SessionLocal() as session:
        print(await sync_catalog(session))


async def cmd_sync_submissions() -> None:
    async with SessionLocal() as session:
        print("новых посылок:", await sync_all(session))


async def make_teacher(name: str) -> None:
    async with SessionLocal() as session:
        user = await session.scalar(select(User).where(User.display_name == name))
        if user is None:
            print(f"пользователь «{name}» не найден")
            return
        user.role = Role.teacher
        await session.commit()
        print(f"{name} теперь преподаватель")


async def stats() -> None:
    from sqlalchemy import func

    from app.models import Problem, Submission

    async with SessionLocal() as session:
        for platform in Platform:
            count = await session.scalar(
                select(func.count()).select_from(Problem).where(Problem.platform == platform)
            )
            print(f"{platform.title}: {count} задач в каталоге")
        print("пользователей:", await session.scalar(select(func.count()).select_from(User)))
        print("посылок:", await session.scalar(select(func.count()).select_from(Submission)))


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(USAGE)
        return 1
    command, *rest = args
    match command:
        case "gen-secret":
            gen_secret()
        case "backup":
            if not rest:
                print("укажи путь к файлу: python -m app.cli backup ./sport-backup.db")
                return 1
            backup(rest[0])
        case "init-db":
            asyncio.run(init_db())
        case "sync-catalog":
            asyncio.run(cmd_sync_catalog())
        case "sync-submissions":
            asyncio.run(cmd_sync_submissions())
        case "make-teacher":
            if not rest:
                print("укажи имя пользователя")
                return 1
            asyncio.run(make_teacher(" ".join(rest)))
        case "stats":
            asyncio.run(stats())
        case _:
            print(USAGE)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
