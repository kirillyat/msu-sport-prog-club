from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select

from app.config import settings
from app.deps import OptionalUser, SessionDep
from app.models import LoginToken, Role, User, utcnow
from app.security import issue_session
from app.templating import templates

router = APIRouter(tags=["auth"])


def _set_session_cookie(response: RedirectResponse, user_id: int) -> RedirectResponse:
    response.set_cookie(
        settings.session_cookie,
        issue_session(user_id),
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=settings.base_url.startswith("https://"),
        path="/",
    )
    return response


async def _is_first_user(session: SessionDep) -> bool:
    """Первый вошедший становится преподавателем.

    Иначе портал запирается: переключатель ролей лежит на странице,
    которая сама требует роли преподавателя.
    """
    return (await session.scalar(select(func.count()).select_from(User))) == 0


async def _resolve_user(session: SessionDep, token: LoginToken) -> User:
    user = await session.scalar(select(User).where(User.telegram_id == token.telegram_id))
    if user is None:
        first = await _is_first_user(session)
        role = (
            Role.teacher
            if (first or token.telegram_id in settings.teacher_ids)
            else Role.student
        )
        user = User(
            display_name=token.display_name or f"tg{token.telegram_id}",
            telegram_id=token.telegram_id,
            telegram_username=token.telegram_username,
            role=role,
        )
        session.add(user)
    else:
        user.telegram_username = token.telegram_username
        # Список преподавателей в .env — источник истины, применяем при каждом входе.
        if token.telegram_id in settings.teacher_ids:
            user.role = Role.teacher
    await session.commit()
    await session.refresh(user)
    return user


@router.get("/login")
async def login_page(request: Request, session: SessionDep, user: OptionalUser):
    if user is not None:
        return RedirectResponse("/", status_code=303)

    token = None
    if settings.telegram_bot_token and settings.telegram_bot_username:
        token = LoginToken(
            code=secrets.token_urlsafe(9).replace("-", "_"),
            expires_at=utcnow() + timedelta(seconds=settings.login_code_ttl_seconds),
        )
        session.add(token)
        await session.commit()

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "code": token.code if token else None,
            "deep_link": settings.telegram_login_url_template.format(code=token.code)
            if token
            else None,
            "dev_login": settings.dev_login_enabled,
            "error": request.query_params.get("err"),
        },
    )


@router.get("/login/status/{code}")
async def login_status(code: str, session: SessionDep):
    token = await session.scalar(select(LoginToken).where(LoginToken.code == code))
    if token is None:
        return JSONResponse({"state": "unknown"})
    if token.consumed_at is not None:
        return JSONResponse({"state": "consumed"})
    if token.expires_at < utcnow():
        return JSONResponse({"state": "expired"})
    if token.confirmed_at is not None:
        return JSONResponse({"state": "confirmed", "next": f"/login/complete/{code}"})
    return JSONResponse({"state": "pending"})


@router.get("/login/complete/{code}")
async def login_complete(code: str, session: SessionDep):
    token = await session.scalar(select(LoginToken).where(LoginToken.code == code))
    now = utcnow()
    if (
        token is None
        or token.confirmed_at is None
        or token.consumed_at is not None
        or token.expires_at < now
        or token.telegram_id is None
    ):
        return RedirectResponse("/login?err=Код+недействителен", status_code=303)

    token.consumed_at = now
    user = await _resolve_user(session, token)
    await session.commit()
    return _set_session_cookie(RedirectResponse("/", status_code=303), user.id)


@router.post("/login/dev")
async def login_dev(session: SessionDep, name: str = Form(...), teacher: bool = Form(False)):
    """Вход без Telegram. Работает только при DEV_LOGIN_ENABLED=true."""
    if not settings.dev_login_enabled:
        return RedirectResponse("/login?err=Dev-вход+выключен", status_code=303)

    name = name.strip()
    if not name:
        return RedirectResponse("/login?err=Введите+имя", status_code=303)

    user = await session.scalar(select(User).where(User.display_name == name))
    if user is None:
        first = await _is_first_user(session)
        user = User(display_name=name, role=Role.teacher if (teacher or first) else Role.student)
        session.add(user)
        await session.commit()
        await session.refresh(user)
    elif teacher and user.role != Role.teacher:
        user.role = Role.teacher
        await session.commit()

    return _set_session_cookie(RedirectResponse("/", status_code=303), user.id)


@router.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(settings.session_cookie, path="/")
    return response
