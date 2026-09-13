from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from sqlalchemy import select

from app import oidc
from app.config import settings
from app.models import Role, User

DISC = oidc.Discovery(
    issuer="https://auth.test/application/o/sport/",
    authorization_endpoint="https://auth.test/application/o/authorize/",
    token_endpoint="https://auth.test/application/o/token/",
    userinfo_endpoint="https://auth.test/application/o/userinfo/",
)


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"h.{body}.s"


@pytest.fixture
def provider(monkeypatch):
    """Настроенный провайдер и подменённые сетевые вызовы."""
    monkeypatch.setattr(settings, "oidc_issuer", DISC.issuer)
    monkeypatch.setattr(settings, "oidc_client_id", "sport")
    monkeypatch.setattr(settings, "oidc_teacher_groups", "sp-teachers")
    monkeypatch.setattr(oidc, "_discovery", None)

    calls: dict = {"exchange": [], "profile": {}}

    async def fake_discover(force=False):
        return DISC

    async def fake_exchange(disc, code, verifier):
        calls["exchange"].append((code, verifier))
        return {"id_token": _jwt(calls["claims"]), "access_token": "at"}

    async def fake_userinfo(disc, token):
        return calls["profile"]

    monkeypatch.setattr(oidc, "discover", fake_discover)
    monkeypatch.setattr(oidc, "exchange_code", fake_exchange)
    monkeypatch.setattr(oidc, "fetch_userinfo", fake_userinfo)
    return calls


async def _start(client) -> tuple[str, str]:
    response = await client.get("/login/oidc", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(DISC.authorization_endpoint)
    query = parse_qs(urlparse(location).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [settings.oidc_redirect_uri]
    return query["state"][0], query["nonce"][0]


async def test_login_button_visible_only_when_configured(session, client, monkeypatch):
    page = await client.get("/login")
    assert "Войти через Authentik" not in page.text
    monkeypatch.setattr(settings, "oidc_issuer", DISC.issuer)
    monkeypatch.setattr(settings, "oidc_client_id", "sport")
    page = await client.get("/login")
    assert "Войти через Authentik" in page.text


async def test_full_flow_creates_user_and_maps_teacher_group(session, client, provider):
    state, nonce = await _start(client)
    provider["claims"] = {"sub": "ak-42", "nonce": nonce, "email": "anya@msu.ru"}
    provider["profile"] = {"name": "Аня Ковалёва", "groups": ["students", "sp-teachers"]}

    # Занимаем место первого пользователя, чтобы роль пришла именно из группы.
    session.add(User(display_name="Кто-то первый"))
    await session.commit()

    response = await client.get(
        f"/login/oidc/callback?code=abc&state={state}", follow_redirects=False
    )
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert settings.session_cookie in response.headers.get("set-cookie", "")

    user = await session.scalar(select(User).where(User.oidc_sub == "ak-42"))
    assert user.display_name == "Аня Ковалёва"
    assert user.email == "anya@msu.ru"
    assert user.role == Role.teacher
    assert provider["exchange"][0][0] == "abc"


async def test_student_without_group_stays_student(session, client, provider):
    session.add(User(display_name="Кто-то первый"))
    await session.commit()
    state, nonce = await _start(client)
    provider["claims"] = {"sub": "ak-7", "nonce": nonce, "preferred_username": "borya"}
    provider["profile"] = {"groups": ["students"]}
    await client.get(f"/login/oidc/callback?code=x&state={state}", follow_redirects=False)
    user = await session.scalar(select(User).where(User.oidc_sub == "ak-7"))
    assert user.display_name == "borya"
    assert user.role == Role.student


async def test_second_login_reuses_user_by_sub(session, client, provider):
    state, nonce = await _start(client)
    provider["claims"] = {"sub": "ak-1", "nonce": nonce, "name": "Аня"}
    await client.get(f"/login/oidc/callback?code=a&state={state}", follow_redirects=False)
    await client.post("/logout")
    state, nonce = await _start(client)
    provider["claims"] = {"sub": "ak-1", "nonce": nonce, "name": "Аня Переименованная"}
    await client.get(f"/login/oidc/callback?code=b&state={state}", follow_redirects=False)
    users = (await session.execute(select(User).where(User.oidc_sub == "ak-1"))).scalars().all()
    assert len(users) == 1


async def test_state_mismatch_is_rejected(session, client, provider):
    _, nonce = await _start(client)
    provider["claims"] = {"sub": "ak-9", "nonce": nonce}
    response = await client.get(
        "/login/oidc/callback?code=x&state=forged", follow_redirects=False
    )
    assert "устарела" in unquote(response.headers["location"])
    assert await session.scalar(select(User)) is None


async def test_nonce_mismatch_is_rejected(session, client, provider):
    state, _ = await _start(client)
    provider["claims"] = {"sub": "ak-9", "nonce": "other"}
    response = await client.get(
        f"/login/oidc/callback?code=x&state={state}", follow_redirects=False
    )
    assert "не совпал" in unquote(response.headers["location"])
    assert await session.scalar(select(User)) is None


async def test_provider_error_is_shown(session, client, provider):
    response = await client.get(
        "/login/oidc/callback?error=access_denied&error_description=Отказано",
        follow_redirects=False,
    )
    assert "Отказано" in unquote(response.headers["location"])


async def test_start_refused_when_not_configured(session, client):
    response = await client.get("/login/oidc", follow_redirects=False)
    assert "не настроен" in unquote(response.headers["location"])


def test_pkce_challenge_matches_verifier():
    import base64 as b64
    import hashlib

    verifier, challenge = oidc.pkce_pair()
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = b64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    assert challenge == expected
