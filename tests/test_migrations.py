"""Миграции и модели должны давать одну и ту же схему.

Тесты поднимают базу из метаданных, а прод — из миграций, поэтому расхождение
между ними тестами не ловится. Особенно это касается CHECK: их Alembic
не сравнивает вовсе, так что `alembic check` на такое расхождение молчит.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, text

from app.models import Base

CHECK = re.compile(r"CONSTRAINT\s+(\w+)\s+CHECK\s*\((.+?)\)\s*(?:,|\n|$)", re.S | re.I)


def _checks(db_path) -> dict[str, str]:
    """Имя ограничения -> его условие без лишних пробелов."""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        rows = conn.execute(
            text("select sql from sqlite_master where type='table' and sql is not null")
        ).scalars()
        out = {}
        for sql in rows:
            for name, body in CHECK.findall(sql):
                out[name] = " ".join(body.split())
    engine.dispose()
    return out


def _from_migrations(tmp_path):
    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path),
        "SECRET_KEY": "тест-только-для-миграций",
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head упал:\n{result.stdout}\n{result.stderr}")
    return tmp_path / "sport.db"


def _from_models(tmp_path):
    path = tmp_path / "models.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    engine.dispose()
    return path


def test_check_constraints_match_the_models(tmp_path):
    migrated = _checks(_from_migrations(tmp_path / "migrated"))
    declared = _checks(_from_models(tmp_path))
    assert migrated == declared


def test_migrations_create_the_same_tables(tmp_path):
    def tables(path):
        engine = create_engine(f"sqlite:///{path}")
        with engine.connect() as conn:
            names = set(conn.execute(
                text("select name from sqlite_master where type='table'")
            ).scalars())
        engine.dispose()
        return names - {"alembic_version", "sqlite_sequence"}

    assert tables(_from_migrations(tmp_path / "migrated")) == tables(_from_models(tmp_path))
