"""Отправка материала в Telegram файлом, а не ссылкой."""

from __future__ import annotations

import pytest

from app import notify
from app.config import settings
from app.models import Group, GroupMembership, Material, User
from app.services import materials

CONTENT = b'{"cells": [], "nbformat": 4}'


@pytest.fixture(autouse=True)
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(materials.settings, "data_dir", tmp_path)
    return tmp_path


@pytest.fixture
def outbox(monkeypatch):
    """Записываем и документы, и обычные сообщения — чтобы видеть, что ушло."""
    sent: dict[str, list] = {"documents": [], "messages": []}

    async def fake_documents(chat_ids, filename, content, caption):
        ids = [str(c) for c in chat_ids]
        sent["documents"].append((ids, filename, content, caption))
        return len(ids)

    async def fake_messages(chat_ids, text):
        ids = [str(c) for c in chat_ids]
        sent["messages"].append((ids, text))
        return len(ids)

    monkeypatch.setattr(notify, "send_document_many", fake_documents)
    monkeypatch.setattr(notify, "send_many", fake_messages)
    monkeypatch.setattr(settings, "telegram_notify_chat_id", "-100777")
    return sent


def _material(stored: str = "abc.ipynb", **kwargs) -> Material:
    defaults = {
        "title": "Семинар 3",
        "stored_name": stored,
        "filename": "seminar03.ipynb",
        "content_type": "application/x-ipynb+json",
        "size": len(CONTENT),
    }
    return Material(**(defaults | kwargs))


async def test_material_goes_to_the_chat_as_a_file(session, outbox, storage):
    materials.save("abc.ipynb", CONTENT)
    item = _material()
    session.add(item)
    await session.commit()
    await session.refresh(item)

    assert await notify.notify_material(item, session) == 1
    chats, filename, content, caption = outbox["documents"][0]
    assert chats == ["-100777"]
    assert (filename, content) == ("seminar03.ipynb", CONTENT)
    assert "Семинар 3" in caption
    assert outbox["messages"] == []


async def test_without_a_chat_the_file_goes_to_each_student(session, outbox, storage, monkeypatch):
    monkeypatch.setattr(settings, "telegram_notify_chat_id", "")
    group = Group(title="Осень", join_code="FILE01")
    inside = User(display_name="Свой", telegram_id=11)
    outside = User(display_name="Чужой", telegram_id=22)
    session.add_all([group, inside, outside])
    await session.commit()
    session.add(GroupMembership(group_id=group.id, user_id=inside.id))

    materials.save("abc.ipynb", CONTENT)
    item = _material(group_id=group.id)
    session.add(item)
    await session.commit()
    await session.refresh(item)

    assert await notify.notify_material(item, session) == 1
    assert outbox["documents"][0][0] == ["11"]


async def test_lost_file_still_sends_a_link(session, outbox, storage):
    """Записи в базе есть, файла на диске нет — студент хотя бы узнает о материале."""
    item = _material(stored="потерялся.ipynb")
    session.add(item)
    await session.commit()
    await session.refresh(item)

    assert await notify.notify_material(item, session) == 1
    assert outbox["documents"] == []
    assert "Семинар 3" in outbox["messages"][0][1]


async def test_oversized_file_is_not_uploaded(monkeypatch):
    """Телеграм принимает от бота до 50 МБ — больше даже не пробуем."""
    monkeypatch.setattr(notify, "TELEGRAM_MAX_DOCUMENT", 10)
    monkeypatch.setattr(settings, "telegram_bot_token", "token")
    assert await notify.send_document_many(["-1"], "big.zip", b"x" * 11, "подпись") == 0
