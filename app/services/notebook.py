"""Показ ноутбука на портале.

Рендерим сами и намеренно бедно: markdown, код и картинки. HTML-вывод ячеек
не показываем вообще — в ноутбуке он произвольный, а страница открывается
на нашем домене под сессией студента. Кому нужен полный вид, скачивает файл.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from typing import Any

from markdown_it import MarkdownIt

# html=False: сырой HTML внутри markdown экранируется, а не выполняется.
_md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})

# Ноутбук больше этого не показываем: страница всё равно будет нечитаемой.
MAX_PREVIEW_BYTES = 4 * 1024 * 1024
MAX_OUTPUT_CHARS = 20_000
SAFE_IMAGES = ("image/png", "image/jpeg", "image/gif", "image/webp")
ANSI = re.compile(r"\x1b\[[0-9;]*m")

PREVIEWABLE = {".ipynb", ".md", ".py", ".txt", ".csv", ".json", ".tex"}


@dataclass(slots=True)
class Output:
    kind: str                 # text | image | skipped
    text: str = ""
    src: str = ""


@dataclass(slots=True)
class Cell:
    kind: str                 # markdown | code | raw
    html: str = ""
    source: str = ""
    language: str = ""
    outputs: list[Output] = field(default_factory=list)


def is_previewable(filename: str, size: int) -> bool:
    from pathlib import Path

    return Path(filename).suffix.lower() in PREVIEWABLE and size <= MAX_PREVIEW_BYTES


def _text(value: Any) -> str:
    """В ipynb строка бывает и списком строк — формат допускает оба вида."""
    if isinstance(value, list):
        return "".join(str(part) for part in value)
    return "" if value is None else str(value)


def render_markdown(source: str) -> str:
    return _md.render(source)


def _output(raw: dict) -> Output | None:
    kind = raw.get("output_type")
    if kind == "stream":
        return Output("text", _text(raw.get("text"))[:MAX_OUTPUT_CHARS])
    if kind == "error":
        trace = ANSI.sub("", _text(raw.get("traceback")))
        return Output("text", trace[:MAX_OUTPUT_CHARS])
    if kind in ("execute_result", "display_data"):
        data = raw.get("data") or {}
        for mime in SAFE_IMAGES:
            if mime in data:
                payload = _text(data[mime]).replace("\n", "")
                try:
                    base64.b64decode(payload, validate=True)
                except (binascii.Error, ValueError):
                    return Output("skipped", "картинка не читается")
                return Output("image", src=f"data:{mime};base64,{payload}")
        if "text/plain" in data:
            return Output("text", _text(data["text/plain"])[:MAX_OUTPUT_CHARS])
        if data:
            return Output("skipped", "вывод в формате " + ", ".join(sorted(data)))
    return None


def parse(content: bytes) -> list[Cell] | None:
    """Разбирает .ipynb. None — файл не разобрался, показывать нечего."""
    try:
        nb = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(nb, dict) or not isinstance(nb.get("cells"), list):
        return None

    language = str(
        ((nb.get("metadata") or {}).get("language_info") or {}).get("name") or "python"
    )
    cells: list[Cell] = []
    for raw in nb["cells"]:
        if not isinstance(raw, dict):
            continue
        source = _text(raw.get("source"))
        kind = raw.get("cell_type")
        if kind == "markdown":
            cells.append(Cell("markdown", html=render_markdown(source)))
        elif kind == "code":
            outputs = [o for o in (_output(r) for r in raw.get("outputs") or []) if o]
            cells.append(Cell("code", source=source, language=language, outputs=outputs))
        else:
            cells.append(Cell("raw", source=source))
    return cells
