from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import notify
from app.config import settings
from app.models import Announcement, Assignment, Group, ProblemSet


@pytest.fixture
def outbox(monkeypatch):
    sent: list[tuple[str, str]] = []

    async def fake_send(chat_id, text):
        sent.append((str(chat_id), text))
        return True

    monkeypatch.setattr(notify, "send", fake_send)
    monkeypatch.setattr(settings, "telegram_notify_chat_id", "-100777")
    return sent


def test_announcement_text_has_title_time_and_link():
    item = Announcement(
        title="Раунд <999>", body="Сбор в 17:45",
        url="https://cf.example/1", url_label="Регистрация",
        starts_at=datetime(2026, 9, 15, 16, 51, tzinfo=UTC),
        ends_at=datetime(2026, 9, 15, 18, 51, tzinfo=UTC),
    )
    text = notify.announcement_text(item)
    assert "<b>Раунд &lt;999&gt;</b>" in text  # HTML экранирован
    assert "15.09.2026 19:51 — 21:51" in text  # московское время
    assert 'href="https://cf.example/1">Регистрация</a>' in text


async def test_announcement_goes_to_group_chat_when_set(session, outbox):
    group = Group(title="А", join_code="AAA111", telegram_chat_id="-100555")
    session.add(group)
    await session.commit()
    item = Announcement(title="Только группе", group_id=group.id)
    session.add(item)
    await session.commit()
    await session.refresh(item)

    assert await notify.notify_announcement(item, session) is True
    assert outbox[0][0] == "-100555"


async def test_announcement_falls_back_to_club_chat(session, outbox):
    item = Announcement(title="Всем")
    session.add(item)
    await session.commit()
    await session.refresh(item)
    assert await notify.notify_announcement(item) is True
    assert outbox[0][0] == "-100777"


async def test_nothing_sent_without_any_chat(session, outbox, monkeypatch):
    monkeypatch.setattr(settings, "telegram_notify_chat_id", "")
    item = Announcement(title="Тихо")
    session.add(item)
    await session.commit()
    await session.refresh(item)
    assert await notify.notify_announcement(item) is False
    assert outbox == []


async def test_reminders_only_inside_window_and_once(session, outbox, monkeypatch):
    monkeypatch.setattr(settings, "reminder_minutes_before", 60)
    now = datetime.now(UTC)
    session.add_all([
        Announcement(title="Скоро", starts_at=now + timedelta(minutes=30)),
        Announcement(title="Позже", starts_at=now + timedelta(hours=5)),
        Announcement(title="Уже идёт", starts_at=now - timedelta(minutes=5)),
        Announcement(title="Уже напомнили", starts_at=now + timedelta(minutes=20), reminded_at=now),
    ])
    await session.commit()

    assert await notify.send_due_reminders(session) == 1
    assert "Скоро" in outbox[0][1] and "Через" in outbox[0][1]

    # Второй круг — ничего нового.
    assert await notify.send_due_reminders(session) == 0
    item = await session.scalar(select(Announcement).where(Announcement.title == "Скоро"))
    assert item.reminded_at is not None


async def test_publishing_announcement_notifies(session, client, outbox):
    await client.post("/login/dev", data={"name": "Кирилл", "teacher": "true"})
    response = await client.post("/teacher/announcements", data={"title": "Тест", "url": ""})
    assert "отправлено в Telegram" in response.text
    assert len(outbox) == 1 and "Тест" in outbox[0][1]


async def test_assignment_text_and_route(session, client, outbox):
    await client.post("/login/dev", data={"name": "Кирилл", "teacher": "true"})
    await client.post("/teacher/groups", data={"title": "Группа"})
    group = await session.scalar(select(Group))
    problem_set = ProblemSet(title="Список")
    session.add(problem_set)
    await session.commit()

    await client.post("/teacher/assignments", data={
        "title": "Неделя 1", "problem_set_id": problem_set.id, "group_id": group.id,
        "deadline": "2026-10-01T18:00",
    })
    assert len(outbox) == 1
    text = outbox[0][1]
    assert "Неделя 1" in text and "Дедлайн: 01.10.2026 18:00" in text and "/assignments/" in text
    assignment = await session.scalar(select(Assignment))
    assert assignment is not None


async def test_group_chat_id_validation(session, client):
    await client.post("/login/dev", data={"name": "Кирилл", "teacher": "true"})
    await client.post("/teacher/groups", data={"title": "Группа"})
    group = await session.scalar(select(Group))

    bad = await client.post(f"/teacher/groups/{group.id}/chat", data={"chat_id": "abc"})
    assert "число" in bad.text
    ok = await client.post(f"/teacher/groups/{group.id}/chat", data={"chat_id": "-1001234"})
    assert "сохранён" in ok.text
    await session.refresh(group)
    assert group.telegram_chat_id == "-1001234"
