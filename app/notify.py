"""Уведомления в Telegram.

Адресат выбирается так: если у группы (или у клуба) задан общий чат — пишем
туда одним сообщением, иначе бот пишет каждому участнику лично. Личное задание
всегда уходит только самому студенту.

Отправка никогда не роняет запрос: ошибка сети — это запись в лог,
а объявление всё равно опубликовано.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import TelegramAPI
from app.config import settings
from app.models import Announcement, Assignment, Group, GroupMembership, User, utcnow
from app.templating import fmt_dt

logger = logging.getLogger(__name__)


def _chat_for(group: Group | None) -> str | None:
    if group is not None and group.telegram_chat_id:
        return group.telegram_chat_id
    return settings.telegram_notify_chat_id or None


async def _group_of(session: AsyncSession | None, group_id: int | None) -> Group | None:
    """Связь после refresh не загружена; тянем группу явно, чтобы не упасть в async."""
    if session is None or group_id is None:
        return None
    return await session.get(Group, group_id)


async def _personal_chats(
    session: AsyncSession, group_id: int | None = None, user_id: int | None = None
) -> list[int]:
    """Личные чаты адресатов. Кто не входил через бота, тому написать некуда."""
    stmt = select(User.telegram_id).where(
        User.telegram_id.is_not(None), User.is_active.is_(True)
    )
    if user_id is not None:
        stmt = stmt.where(User.id == user_id)
    elif group_id is not None:
        stmt = stmt.join(GroupMembership, GroupMembership.user_id == User.id).where(
            GroupMembership.group_id == group_id
        )
    # Иначе адресат — весь клуб.
    return list((await session.execute(stmt)).scalars().all())


async def send_many(chat_ids: Iterable[str | int], text: str) -> int:
    """Одна рассылка — один HTTP-клиент. Недоступный адресат не отменяет остальных."""
    ids = list(chat_ids)
    if not ids or not settings.telegram_bot_token:
        return 0
    api = TelegramAPI(settings.telegram_bot_token)
    sent = 0
    try:
        for chat_id in ids:
            result = await api.call(
                "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
                disable_web_page_preview=True,
            )
            if result is not None:
                sent += 1
    finally:
        await api.close()
    return sent


async def send(chat_id: str | int, text: str) -> bool:
    return await send_many([chat_id], text) > 0


async def _deliver(
    session: AsyncSession | None,
    text: str,
    group_id: int | None = None,
    user_id: int | None = None,
) -> int:
    """Общий чат, если он задан; иначе — каждому лично."""
    if user_id is None:
        chat = _chat_for(await _group_of(session, group_id))
        if chat:
            return await send_many([chat], text)
    if session is None:
        return 0
    return await send_many(await _personal_chats(session, group_id, user_id), text)


def _e(value: object) -> str:
    return html.escape(str(value or ""))


def announcement_text(item: Announcement) -> str:
    lines = [f"📣 <b>{_e(item.title)}</b>"]
    if item.starts_at:
        when = fmt_dt(item.starts_at)
        if item.ends_at:
            when += f" — {fmt_dt(item.ends_at, '%H:%M')}"
        lines.append(f"🕐 {when}")
    if item.body:
        lines.append("")
        lines.append(_e(item.body))
    if item.url:
        lines.append("")
        lines.append(f'<a href="{_e(item.url)}">{_e(item.url_label or "Перейти")}</a>')
    return "\n".join(lines)


def reminder_text(item: Announcement) -> str:
    minutes = max(1, round((item.starts_at - utcnow()).total_seconds() / 60))
    lines = [
        f"⏰ Через {minutes} мин: <b>{_e(item.title)}</b>",
        f"🕐 старт {fmt_dt(item.starts_at, '%H:%M')}",
    ]
    if item.url:
        lines.append(f'<a href="{_e(item.url)}">{_e(item.url_label or "Перейти")}</a>')
    return "\n".join(lines)


def assignment_text(item: Assignment, problems: int) -> str:
    lines = [f"📝 Новое задание: <b>{_e(item.title)}</b>", f"Задач: {problems}"]
    if item.deadline:
        suffix = " (после срока не засчитывается)" if item.hard_deadline else ""
        lines.append(f"Дедлайн: {fmt_dt(item.deadline)}{suffix}")
    base = settings.base_url.rstrip("/")
    lines.append(f'<a href="{_e(base)}/assignments/{item.id}">Открыть на портале</a>')
    return "\n".join(lines)


async def notify_announcement(item: Announcement, session: AsyncSession | None = None) -> int:
    return await _deliver(session, announcement_text(item), group_id=item.group_id)


async def notify_assignment(
    item: Assignment, problems: int, session: AsyncSession | None = None
) -> int:
    return await _deliver(
        session, assignment_text(item, problems),
        group_id=item.group_id, user_id=item.user_id,
    )


async def send_due_reminders(session: AsyncSession) -> int:
    """Напоминания о событиях, которые начнутся в ближайшие N минут."""
    now = utcnow()
    horizon = now + timedelta(minutes=settings.reminder_minutes_before)
    stmt = select(Announcement).where(
        Announcement.starts_at.is_not(None),
        Announcement.starts_at > now,
        Announcement.starts_at <= horizon,
        Announcement.reminded_at.is_(None),
    )
    sent = 0
    for item in (await session.execute(stmt)).scalars().all():
        # Отмечаем в любом случае: иначе каждый круг будем перебирать одно и то же.
        item.reminded_at = now
        if await _deliver(session, reminder_text(item), group_id=item.group_id):
            sent += 1
    await session.commit()
    return sent
