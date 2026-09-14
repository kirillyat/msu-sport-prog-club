"""Файлы материалов: где лежат, что принимаем, как отдаём.

Файл кладём на диск рядом с базой под случайным именем. Пользовательское имя
в путь не попадает вообще — это самый простой способ не думать про «../».
"""

from __future__ import annotations

import secrets
from pathlib import Path

from app.config import settings

MAX_BYTES = 25 * 1024 * 1024

# Что имеет смысл раздавать студентам. HTML и SVG намеренно нет: они выполнили бы
# скрипт на нашем домене, а браузеру мы и так велим скачивать, а не открывать.
ALLOWED = {
    ".ipynb": "application/x-ipynb+json",
    ".py": "text/x-python",
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".zip": "application/zip",
    ".tex": "application/x-tex",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

EXTENSIONS_HINT = "ipynb, py, pdf, md, txt, csv, json, zip, tex, png, jpg"


def storage_dir() -> Path:
    path = settings.data_dir / "materials"
    path.mkdir(parents=True, exist_ok=True)
    return path


def extension_of(filename: str) -> str:
    return Path(filename or "").suffix.lower()


def is_allowed(filename: str) -> bool:
    return extension_of(filename) in ALLOWED


def content_type_for(filename: str) -> str:
    return ALLOWED.get(extension_of(filename), "application/octet-stream")


def safe_filename(filename: str) -> str:
    """Оставляем только имя файла: каталоги из него выкидываем."""
    name = Path(filename or "").name.strip()
    return name[:200] or "material"


def new_stored_name(filename: str) -> str:
    return secrets.token_hex(16) + extension_of(filename)


def path_for(stored_name: str) -> Path:
    # Имя мы сгенерировали сами, но проверка дешёвая, а ошибка дорогая.
    return storage_dir() / Path(stored_name).name


def save(stored_name: str, data: bytes) -> None:
    path_for(stored_name).write_bytes(data)


def remove(stored_name: str) -> None:
    path_for(stored_name).unlink(missing_ok=True)
