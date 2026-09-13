from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.config import settings
from app.deps import CurrentUser, SessionDep
from app.models import Platform, PlatformAccount
from app.services import verification
from app.services.sync import sync_account
from app.templating import templates

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _back(message: str | None = None, error: str | None = None) -> RedirectResponse:
    params = []
    if message:
        params.append(f"ok={message}")
    if error:
        params.append(f"err={error}")
    suffix = ("?" + "&".join(params)) if params else ""
    return RedirectResponse(f"/accounts{suffix}", status_code=303)


@router.get("")
async def accounts_page(request: Request, user: CurrentUser):
    return templates.TemplateResponse(
        request,
        "accounts.html",
        {
            "user": user,
            "platforms": list(Platform),
            "where_to_put": verification.WHERE_TO_PUT,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("err"),
        },
    )


def _login_methods(user) -> int:
    """Сколько способов входа осталось. Последний отвязывать нельзя."""
    return sum(1 for value in (user.telegram_id, user.oidc_sub) if value)


@router.post("/telegram/unlink")
async def unlink_telegram(session: SessionDep, user: CurrentUser):
    if user.telegram_id is None:
        return _back(error="Telegram не привязан")
    if _login_methods(user) < 2:
        return _back(error="Это единственный способ входа — сначала привяжи другой")
    user.telegram_id = None
    user.telegram_username = None
    await session.commit()
    return _back(message="Telegram отвязан")


@router.post("/oidc/unlink")
async def unlink_oidc(session: SessionDep, user: CurrentUser):
    if user.oidc_sub is None:
        return _back(error=f"{settings.oidc_provider_name} не привязан")
    if _login_methods(user) < 2:
        return _back(error="Это единственный способ входа — сначала привяжи другой")
    user.oidc_sub = None
    await session.commit()
    return _back(message=f"{settings.oidc_provider_name} отвязан")


@router.post("/link")
async def link_account(
    session: SessionDep,
    user: CurrentUser,
    platform: str = Form(...),
    handle: str = Form(...),
):
    try:
        target = Platform(platform)
    except ValueError:
        return _back(error="Неизвестная платформа")

    try:
        await verification.start_verification(session, user, target, handle)
    except ValueError as exc:
        return _back(error=str(exc))
    return _back(message="Код выдан, впиши его в профиль и нажми «Проверить»")


@router.post("/{account_id}/verify")
async def verify_account(session: SessionDep, user: CurrentUser, account_id: int):
    account = await session.get(PlatformAccount, account_id)
    if account is None or account.user_id != user.id:
        return _back(error="Аккаунт не найден")
    try:
        confirmed = await verification.confirm_verification(session, account)
    except ValueError as exc:
        return _back(error=str(exc))
    if not confirmed:
        return _back(error="Код в профиле не найден. Сохранил ли ты изменения?")

    await sync_account(session, account)
    return _back(message=f"{account.platform.title} привязан, посылки загружаются")


@router.post("/{account_id}/sync")
async def sync_now(session: SessionDep, user: CurrentUser, account_id: int):
    account = await session.get(PlatformAccount, account_id)
    if account is None or account.user_id != user.id:
        return _back(error="Аккаунт не найден")
    if not account.is_verified:
        return _back(error="Сначала подтверди аккаунт")
    added = await sync_account(session, account)
    if account.last_sync_error:
        return _back(error=account.last_sync_error)
    return _back(message=f"Синхронизировано, новых посылок: {added}")


@router.post("/{account_id}/unlink")
async def unlink_account(session: SessionDep, user: CurrentUser, account_id: int):
    account = await session.get(PlatformAccount, account_id)
    if account is None or account.user_id != user.id:
        return _back(error="Аккаунт не найден")
    await session.delete(account)
    await session.commit()
    return _back(message="Аккаунт отвязан")


@router.get("/handles/{platform}")
async def taken_handles(session: SessionDep, user: CurrentUser, platform: str):
    """Служебный эндпоинт: какие хэндлы уже заняты (чтобы не гадать при привязке)."""
    try:
        target = Platform(platform)
    except ValueError:
        return {"handles": []}
    rows = await session.execute(
        select(PlatformAccount.handle).where(
            PlatformAccount.platform == target, PlatformAccount.verified_at.is_not(None)
        )
    )
    return {"handles": sorted(rows.scalars().all())}
