"""Уведомления в Telegram: чат клуба или чат группы.

Отправка никогда не роняет запрос: ошибка сети — это запись в лог,
а объявление всё равно опубликовано.
"""

from __future__ import annotations

import html
import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot import TelegramAPI
from app.config import settings
from app.models import Announcement, Assignment, Group, utcnow
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


async def send(chat_id: str | int, text: str) -> bool:
    if not settings.telegram_bot_token:
        return False
    api = TelegramAPI(settings.telegram_bot_token)
    try:
        result = await api.call(
            "sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
            disable_web_page_preview=True,
        )
        return result is not None
    finally:
        await api.close()


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


async def notify_announcement(item: Announcement, session: AsyncSession | None = None) -> bool:
    chat = _chat_for(await _group_of(session, item.group_id))
    if not chat:
        return False
    return await send(chat, announcement_text(item))


async def notify_assignment(
    item: Assignment, problems: int, session: AsyncSession | None = None
) -> bool:
    chat = _chat_for(await _group_of(session, item.group_id))
    if not chat:
        return False
    return await send(chat, assignment_text(item, problems))


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
        chat = _chat_for(await _group_of(session, item.group_id))
        # Отмечаем даже без чата: иначе каждый круг будем перебирать одно и то же.
        item.reminded_at = now
        if chat and await send(chat, reminder_text(item)):
            sent += 1
    await session.commit()
    return sent
