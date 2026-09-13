from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import Announcement, Group, GroupMembership, User


async def _login(client, name, teacher=False):
    data = {"name": name}
    if teacher:
        data["teacher"] = "true"
    await client.post("/login/dev", data=data)


async def test_ticker_prefers_live_over_upcoming(session, client):
    await _login(client, "Кирилл", teacher=True)
    now = datetime.now(UTC)
    session.add_all([
        Announcement(title="Через неделю", starts_at=now + timedelta(days=7)),
        Announcement(title="Идёт", starts_at=now - timedelta(hours=1),
                     ends_at=now + timedelta(hours=1)),
        Announcement(title="Завтра", starts_at=now + timedelta(days=1)),
    ])
    await session.commit()

    page = await client.get("/")
    assert 'class="ticker"' in page.text
    assert "Идёт" in page.text.split('class="ticker"', 1)[1].split("</a>", 1)[0]


async def test_ticker_picks_nearest_upcoming(session, client):
    await _login(client, "Кирилл", teacher=True)
    now = datetime.now(UTC)
    session.add_all([
        Announcement(title="Через неделю", starts_at=now + timedelta(days=7)),
        Announcement(title="Завтра", starts_at=now + timedelta(days=1)),
    ])
    await session.commit()

    page = await client.get("/leaderboard")
    strip = page.text.split('class="ticker"', 1)[1].split("</a>", 1)[0]
    assert "Завтра" in strip and "Через неделю" not in strip


async def test_ticker_hidden_without_timed_events(session, client):
    await _login(client, "Кирилл", teacher=True)
    session.add(Announcement(title="Просто новость"))
    await session.commit()
    page = await client.get("/")
    assert 'class="ticker"' not in page.text


async def test_ticker_respects_group_visibility(session, client):
    await _login(client, "Кирилл", teacher=True)
    await client.post("/logout")
    await _login(client, "Вова")
    vova = await session.scalar(select(User).where(User.display_name == "Вова"))

    group = Group(title="Чужая", join_code="ZZZ999")
    session.add(group)
    await session.commit()
    session.add(GroupMembership(group_id=group.id, user_id=vova.id + 1000))  # никого из наших
    session.add(Announcement(title="Не для Вовы", group_id=group.id,
                             starts_at=datetime.now(UTC) + timedelta(days=1)))
    await session.commit()

    page = await client.get("/")
    assert 'class="ticker"' not in page.text
