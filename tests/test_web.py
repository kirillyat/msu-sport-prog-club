from __future__ import annotations

import httpx
from sqlalchemy import select

from app.models import Group, Platform, Problem, Role, User


async def _login(client: httpx.AsyncClient, name: str, teacher: bool = False):
    data = {"name": name}
    if teacher:
        data["teacher"] = "true"
    return await client.post("/login/dev", data=data)


async def test_anonymous_is_sent_to_login(session, client):
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


async def test_dev_login_creates_user_with_role(session, client):
    await _login(client, "Кирилл", teacher=True)
    user = await session.scalar(select(User).where(User.display_name == "Кирилл"))
    assert user.role == Role.teacher

    response = await client.get("/teacher")
    assert response.status_code == 200
    assert "Панель преподавателя" in response.text


async def test_first_user_becomes_teacher(session, client):
    """Иначе портал запирается: сменить роль можно только со страницы для преподавателя."""
    await _login(client, "Аня")  # без галочки «преподаватель»
    user = await session.scalar(select(User).where(User.display_name == "Аня"))
    assert user.role == Role.teacher

    await client.post("/logout")
    await _login(client, "Боря")
    second = await session.scalar(select(User).where(User.display_name == "Боря"))
    assert second.role == Role.student


async def test_student_cannot_open_teacher_pages(session, client):
    await _login(client, "Кирилл", teacher=True)  # занимаем место первого
    await client.post("/logout")
    await _login(client, "Аня")

    response = await client.get("/teacher/assignments")
    assert response.status_code == 403
    assert "только для преподавателя" in response.text
    # Навигация на странице ошибки сохраняется — иначе пользователь в тупике.
    assert "Лидерборд" in response.text


async def test_logout_clears_session(session, client):
    await _login(client, "Аня")
    assert (await client.get("/")).status_code == 200
    await client.post("/logout")
    response = await client.get("/", follow_redirects=False)
    assert response.status_code == 303


async def test_teacher_creates_group_and_student_joins(session, client):
    await _login(client, "Кирилл", teacher=True)
    await client.post("/teacher/groups", data={"title": "Алгоритмы"})
    group = await session.scalar(select(Group))
    assert group is not None and len(group.join_code) == 6

    await client.post("/logout")
    await _login(client, "Аня")
    response = await client.post("/groups/join", data={"join_code": group.join_code})
    assert "Ты в группе" in response.text
    assert "Алгоритмы" in (await client.get("/")).text


async def test_join_with_wrong_code_is_rejected(session, client):
    await _login(client, "Аня")
    response = await client.post("/groups/join", data={"join_code": "НЕТУ1"})
    assert "не найден" in response.text


async def test_full_teacher_flow_renders_matrix(session, client):
    session.add_all([
        Problem(platform=Platform.codeforces, external_id="4A", slug="4a", title="Watermelon",
                url="https://codeforces.com/problemset/problem/4/A", rating=800),
        Problem(platform=Platform.leetcode, external_id="1", slug="two-sum", title="Two Sum",
                url="https://leetcode.com/problems/two-sum/", difficulty="Easy"),
    ])
    await session.commit()

    await _login(client, "Кирилл", teacher=True)
    await client.post("/teacher/groups", data={"title": "Алгоритмы"})
    group = await session.scalar(select(Group))

    created = await client.post("/teacher/sets", data={
        "title": "Разминка", "description": "",
        "problems_text": "cf:4A\nlc:two-sum\nчего-то-нет",
    })
    assert "Не распознано" in created.text  # про несуществующую строку сказали честно
    assert "Watermelon" in created.text and "Two Sum" in created.text

    from app.models import ProblemSet
    problem_set = await session.scalar(select(ProblemSet))
    response = await client.post("/teacher/assignments", data={
        "title": "Неделя 1", "problem_set_id": problem_set.id,
        "group_id": group.id, "deadline": "",
    })
    assert "Неделя 1" in response.text
    assert "В группе нет студентов" in response.text


async def test_assignment_hidden_from_outsiders(session, client):
    from app.models import Assignment, ProblemSet

    problem_set = ProblemSet(title="Список")
    session.add(problem_set)
    await session.commit()
    owner = User(display_name="Аня")
    session.add(owner)
    await session.commit()
    assignment = Assignment(title="Личное", problem_set_id=problem_set.id, user_id=owner.id)
    session.add(assignment)
    await session.commit()

    await _login(client, "Боря")
    response = await client.get(f"/assignments/{assignment.id}")
    assert "не для тебя" in response.text


async def test_dev_login_disabled_by_default(session, client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "dev_login_enabled", False)
    response = await client.post("/login/dev", data={"name": "Хакер"})
    assert "выключен" in response.text
    assert await session.scalar(select(User).where(User.display_name == "Хакер")) is None


async def test_healthz(session, client):
    assert (await client.get("/healthz")).json() == {"status": "ok"}


async def test_profile_sync_button_updates_own_results(session, client, monkeypatch):
    from datetime import UTC, datetime

    from app.models import Platform, PlatformAccount, Role
    from app.routers import student as student_router

    # Аккаунт заводим до входа: тестовая сессия одна на всё, и коллекция
    # accounts у уже загруженного пользователя не обновится сама.
    user = User(display_name="Аня", role=Role.teacher)
    session.add(user)
    await session.commit()
    session.add(PlatformAccount(user_id=user.id, platform=Platform.leetcode,
                                handle="anya", verified_at=datetime.now(UTC)))
    await session.commit()
    await _login(client, "Аня")

    called = []

    async def fake_sync(_session, account):
        called.append(account.handle)
        account.last_sync_error = None
        return 3

    monkeypatch.setattr(student_router, "sync_account", fake_sync)
    response = await client.post(f"/u/{user.id}/sync")
    assert called == ["anya"]
    assert "Обновлено: 3 новых решения" in response.text


async def test_profile_sync_requires_verified_account(session, client):
    await _login(client, "Аня", teacher=True)
    user = await session.scalar(select(User).where(User.display_name == "Аня"))
    response = await client.post(f"/u/{user.id}/sync")
    assert "Нет подтверждённых аккаунтов" in response.text


async def test_student_cannot_sync_someone_else(session, client):
    await _login(client, "Кирилл", teacher=True)
    await client.post("/logout")
    await _login(client, "Аня")
    await client.post("/logout")
    await _login(client, "Боря")

    anya = await session.scalar(select(User).where(User.display_name == "Аня"))
    response = await client.post(f"/u/{anya.id}/sync")
    assert "Чужой профиль обновить нельзя" in response.text


def test_startup_refuses_default_secret(monkeypatch):
    """Дефолтный ключ в проде = подделываемые сессии. Лучше не стартовать вовсе."""
    import pytest

    from app.config import INSECURE_SECRET, settings
    from app.main import check_startup_config

    monkeypatch.setattr(settings, "secret_key", INSECURE_SECRET)
    monkeypatch.setattr(settings, "allow_insecure_secret", False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        check_startup_config()


def test_startup_allows_default_secret_when_explicitly_permitted(monkeypatch):
    from app.config import INSECURE_SECRET, settings
    from app.main import check_startup_config

    monkeypatch.setattr(settings, "secret_key", INSECURE_SECRET)
    monkeypatch.setattr(settings, "allow_insecure_secret", True)
    check_startup_config()


def test_startup_accepts_real_secret(monkeypatch):
    from app.config import settings
    from app.main import check_startup_config

    monkeypatch.setattr(settings, "secret_key", "a-real-generated-key")
    monkeypatch.setattr(settings, "allow_insecure_secret", False)
    check_startup_config()


async def test_login_page_wires_telegram_script(session, client, monkeypatch):
    """Вкладка t.me закрывается скриптом — разметка для него должна быть на месте."""
    from app.config import settings

    monkeypatch.setattr(settings, "telegram_bot_token", "t")
    monkeypatch.setattr(settings, "telegram_bot_username", "sport_bot")

    page = await client.get("/login")
    assert "data-tg-code=" in page.text
    assert "data-tg-link" in page.text
    assert "telegram-login.js" in page.text
    # Инлайновых скриптов на странице не осталось — логика в одном файле.
    assert "<script>" not in page.text.split("</head>", 1)[1]
