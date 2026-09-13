from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.deps import CurrentUser, SessionDep
from app.models import Assignment, Event, Group, GroupMembership, Platform, User, utcnow
from app.routers.announcements import upcoming_for_dashboard
from app.services.feed import build_feed
from app.services.leaderboard import build_leaderboard
from app.services.progress import (
    assignments_for_user,
    compute_progress,
    groups_for_user,
    problems_for_set,
)
from app.services.stats import user_stats
from app.services.sync import sync_account
from app.templating import plural_ru, templates

router = APIRouter(tags=["student"])


@router.get("/")
async def dashboard(request: Request, session: SessionDep, user: CurrentUser):
    assignments = await assignments_for_user(session, user)
    groups = await groups_for_user(session, user)

    cards = []
    for assignment in assignments:
        progress = await compute_progress(session, assignment, [user])
        cards.append(
            {
                "assignment": assignment,
                "progress": progress,
                "solved": progress.solved_count(user.id),
                "total": progress.total_problems,
                "overdue": assignment.deadline is not None
                and assignment.deadline < utcnow()
                and progress.solved_count(user.id) < progress.total_problems,
            }
        )

    group_ids = [g.id for g in groups]
    events_stmt = select(Event).where(Event.ends_at >= utcnow())
    if group_ids:
        events_stmt = events_stmt.where(
            (Event.group_id.is_(None)) | (Event.group_id.in_(group_ids))
        )
    else:
        events_stmt = events_stmt.where(Event.group_id.is_(None))
    events = list((await session.execute(events_stmt.order_by(Event.starts_at))).scalars().all())

    missing_accounts = [
        p
        for p in Platform
        if (account := user.account_for(p)) is None or not account.is_verified
    ]

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "user": user,
            "cards": cards,
            "feed_items": await build_feed(session, user, limit=12),
            "announcements": await upcoming_for_dashboard(session, user),
            "groups": groups,
            "events": events,
            "missing_accounts": missing_accounts,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("err"),
        },
    )


@router.get("/assignments/{assignment_id}")
async def assignment_detail(
    request: Request, session: SessionDep, user: CurrentUser, assignment_id: int
):
    assignment = await session.get(Assignment, assignment_id)
    if assignment is None:
        return RedirectResponse("/?err=Задание+не+найдено", status_code=303)

    if not user.is_teacher:
        allowed = assignment.user_id == user.id
        if not allowed and assignment.group_id is not None:
            member = await session.scalar(
                select(GroupMembership).where(
                    GroupMembership.group_id == assignment.group_id,
                    GroupMembership.user_id == user.id,
                )
            )
            allowed = member is not None
        if not allowed:
            return RedirectResponse("/?err=Это+задание+не+для+тебя", status_code=303)

    progress = await compute_progress(session, assignment, [user])
    return templates.TemplateResponse(
        request,
        "assignment.html",
        {
            "user": user,
            "assignment": assignment,
            "progress": progress,
            "problems": progress.problems,
        },
    )


@router.get("/events/{event_id}")
async def event_detail(request: Request, session: SessionDep, user: CurrentUser, event_id: int):
    event = await session.get(Event, event_id)
    if event is None:
        return RedirectResponse("/?err=Ивент+не+найден", status_code=303)
    problems = await problems_for_set(session, event.problem_set_id)
    return templates.TemplateResponse(
        request,
        "event.html",
        {"user": user, "event": event, "problems": problems},
    )


@router.get("/me")
async def my_profile(request: Request, session: SessionDep, user: CurrentUser):
    return await _profile(request, session, user, user)


@router.get("/u/{user_id}")
async def public_profile(request: Request, session: SessionDep, user: CurrentUser, user_id: int):
    target = await session.get(User, user_id)
    if target is None:
        return RedirectResponse("/?err=Студент+не+найден", status_code=303)
    return await _profile(request, session, user, target)


async def _profile(request: Request, session: SessionDep, viewer: User, target: User):
    stats = await user_stats(session, target)
    groups = await groups_for_user(session, target)

    # Место в рейтинге и баллы — иначе профиль живёт отдельно от клуба.
    board = await build_leaderboard(session)
    place = next((i + 1 for i, row in enumerate(board) if row.user.id == target.id), None)
    score = next((row for row in board if row.user.id == target.id), None)

    # Прогресс по заданиям: главное, что преподаватель хочет увидеть у студента.
    assignment_cards = []
    for assignment in (await assignments_for_user(session, target))[:8]:
        progress = await compute_progress(session, assignment, [target])
        solved = progress.solved_count(target.id)
        assignment_cards.append(
            {
                "assignment": assignment,
                "solved": solved,
                "total": progress.total_problems,
                "overdue": assignment.deadline is not None
                and assignment.deadline < utcnow()
                and solved < progress.total_problems,
            }
        )

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "user": viewer,
            "target": target,
            "stats": stats,
            "groups": groups,
            "place": place,
            "place_of": len(board),
            "score": score,
            "assignment_cards": assignment_cards,
            "recent": await build_feed(session, viewer, only_user=target, limit=5),
            "is_self": viewer.id == target.id,
            "can_rename": viewer.id == target.id or viewer.is_teacher,
            "can_sync": bool(
                (viewer.id == target.id or viewer.is_teacher)
                and [a for a in target.accounts if a.is_verified]
            ),
            "last_synced": max(
                (a.last_synced_at for a in target.accounts if a.last_synced_at), default=None
            ),
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("err"),
        },
    )


@router.post("/u/{user_id}/sync")
async def sync_profile(session: SessionDep, user: CurrentUser, user_id: int):
    target = await session.get(User, user_id)
    if target is None:
        return RedirectResponse("/?err=Профиль+не+найден", status_code=303)
    if target.id != user.id and not user.is_teacher:
        return RedirectResponse(f"/u/{user_id}?err=Чужой+профиль+обновить+нельзя", status_code=303)

    back = "/me" if target.id == user.id else f"/u/{target.id}"
    accounts = [account for account in target.accounts if account.is_verified]
    if not accounts:
        return RedirectResponse(f"{back}?err=Нет+подтверждённых+аккаунтов", status_code=303)

    added = 0
    errors = []
    for account in accounts:
        added += await sync_account(session, account)
        if account.last_sync_error:
            errors.append(f"{account.platform.title}: {account.last_sync_error}")

    if errors:
        return RedirectResponse(f"{back}?err=" + quote("; ".join(errors)), status_code=303)
    word = plural_ru(added, "новое решение", "новых решения", "новых решений")
    return RedirectResponse(f"{back}?ok=" + quote(f"Обновлено: {added} {word}"), status_code=303)


MAX_NAME = 120


@router.post("/u/{user_id}/name")
async def rename_user(
    session: SessionDep, user: CurrentUser, user_id: int, display_name: str = Form(...)
):
    """Своё имя правит любой, чужое — только преподаватель."""
    target = await session.get(User, user_id)
    if target is None:
        return RedirectResponse("/?err=Профиль+не+найден", status_code=303)
    if target.id != user.id and not user.is_teacher:
        return RedirectResponse(f"/u/{user_id}?err=Чужое+имя+менять+нельзя", status_code=303)

    back = "/me" if target.id == user.id else f"/u/{target.id}"
    name = " ".join(display_name.split())
    if not name:
        return RedirectResponse(f"{back}?err=" + quote("Имя не может быть пустым"), status_code=303)
    if len(name) > MAX_NAME:
        return RedirectResponse(
            f"{back}?err=" + quote(f"Не длиннее {MAX_NAME} символов"), status_code=303
        )
    if name == target.display_name:
        return RedirectResponse(back, status_code=303)

    # Тёзки путают матрицу и ленту, а в dev-режиме ещё и вход по имени.
    taken = await session.scalar(
        select(User).where(
            func.lower(User.display_name) == name.lower(),
            User.id != target.id,
            User.is_active.is_(True),
        )
    )
    if taken is not None:
        return RedirectResponse(f"{back}?err=" + quote("Такое имя уже занято"), status_code=303)

    target.display_name = name
    await session.commit()
    return RedirectResponse(f"{back}?ok=" + quote("Имя изменено"), status_code=303)


@router.post("/groups/join")
async def join_group(session: SessionDep, user: CurrentUser, join_code: str = Form(...)):
    code = join_code.strip()
    group = await session.scalar(select(Group).where(Group.join_code == code))
    if group is None or group.is_archived:
        return RedirectResponse("/?err=Код+группы+не+найден", status_code=303)

    existing = await session.scalar(
        select(GroupMembership).where(
            GroupMembership.group_id == group.id, GroupMembership.user_id == user.id
        )
    )
    if existing is None:
        session.add(GroupMembership(group_id=group.id, user_id=user.id))
        await session.commit()
    return RedirectResponse(f"/?ok=Ты+в+группе+«{group.title}»", status_code=303)
