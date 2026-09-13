from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models import (
    Assignment,
    BonusPoint,
    Group,
    GroupMembership,
    Platform,
    PlatformAccount,
    Problem,
    ProblemSet,
    ProblemSetItem,
    SolveStatus,
    Submission,
    User,
)
from app.services import scoring
from app.services.leaderboard import build_leaderboard

BASE = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def test_problem_points_by_difficulty():
    easy = Problem(platform=Platform.leetcode, external_id="1", slug="a", title="A",
                   url="", difficulty="Easy")
    hard = Problem(platform=Platform.leetcode, external_id="2", slug="b", title="B",
                   url="", difficulty="Hard")
    cf = Problem(platform=Platform.codeforces, external_id="4A", slug="4a", title="W",
                 url="", rating=1600)
    unrated = Problem(platform=Platform.codeforces, external_id="9Z", slug="9z", title="U", url="")

    assert scoring.problem_points(easy) == 1.0
    assert scoring.problem_points(hard) == 5.0
    assert scoring.problem_points(cf) == 8.0
    assert scoring.problem_points(unrated) == scoring.CODEFORCES_FALLBACK


def test_late_solve_is_halved():
    problem = Problem(platform=Platform.leetcode, external_id="1", slug="a", title="A",
                      url="", difficulty="Medium")
    assert scoring.points_for(problem, SolveStatus.solved_in_time) == 3.0
    assert scoring.points_for(problem, SolveStatus.solved_late) == 1.5
    assert scoring.points_for(problem, SolveStatus.solved_before) == 0.0
    assert scoring.points_for(problem, SolveStatus.not_solved) == 0.0


@pytest.fixture
async def world(session):
    anya, borya = User(display_name="Аня"), User(display_name="Боря")
    session.add_all([anya, borya])
    await session.commit()

    group = Group(title="Группа", join_code="ABC123")
    session.add(group)
    await session.commit()
    session.add_all([
        GroupMembership(group_id=group.id, user_id=anya.id),
        GroupMembership(group_id=group.id, user_id=borya.id),
    ])

    problems = [
        Problem(platform=Platform.leetcode, external_id=str(i), slug=f"p{i}", title=f"З{i}",
                url="", difficulty="Medium")
        for i in (1, 2)
    ]
    session.add_all(problems)
    await session.commit()

    problem_set = ProblemSet(title="Список")
    session.add(problem_set)
    await session.commit()
    session.add_all([
        ProblemSetItem(problem_set_id=problem_set.id, problem_id=p.id, position=i)
        for i, p in enumerate(problems)
    ])

    accounts = {}
    for user in (anya, borya):
        acc = PlatformAccount(user_id=user.id, platform=Platform.leetcode,
                              handle=user.display_name, verified_at=BASE)
        session.add(acc)
        await session.commit()
        accounts[user.id] = acc

    return {"anya": anya, "borya": borya, "group": group,
            "problems": problems, "set": problem_set, "accounts": accounts}


async def _accept(session, world, user, problem, when, external_id):
    session.add(Submission(
        user_id=user.id, platform_account_id=world["accounts"][user.id].id,
        platform=Platform.leetcode, external_id=external_id, problem_id=problem.id,
        problem_slug=problem.slug, verdict="Accepted", is_accepted=True, submitted_at=when,
    ))
    await session.commit()


async def test_first_blood_and_full_clear(session, world):
    session.add(Assignment(title="З", problem_set_id=world["set"].id,
                           group_id=world["group"].id, assigned_at=BASE))
    await session.commit()

    anya, borya = world["anya"], world["borya"]
    p1, p2 = world["problems"]
    # Аня решает обе, первой по обеим. Боря — только первую, позже.
    await _accept(session, world, anya, p1, BASE + timedelta(hours=1), "a1")
    await _accept(session, world, anya, p2, BASE + timedelta(hours=2), "a2")
    await _accept(session, world, borya, p1, BASE + timedelta(hours=3), "b1")

    rows = await build_leaderboard(session)
    by_name = {r.user.display_name: r for r in rows}

    # Аня: 3 + 3 за задачи, +2 +2 first blood, +5 за полный комплект.
    assert by_name["Аня"].assignment_points == 15.0
    assert by_name["Аня"].first_bloods == 2
    assert by_name["Аня"].full_clears == 1
    # Боря: 3 за задачу, first blood не его, комплект не собран.
    assert by_name["Боря"].assignment_points == 3.0
    assert rows[0].user.display_name == "Аня"


async def test_prior_solve_earns_nothing(session, world):
    session.add(Assignment(title="З", problem_set_id=world["set"].id,
                           group_id=world["group"].id, assigned_at=BASE))
    await session.commit()
    await _accept(session, world, world["anya"], world["problems"][0],
                  BASE - timedelta(days=30), "old")

    rows = await build_leaderboard(session)
    assert all(r.total == 0 for r in rows)


async def test_marathon_counts_only_inside_window(session, world):
    """Марафон — это задание с жёстким дедлайном и одинаковой ценой задач."""
    session.add(Assignment(
        title="Марафон", problem_set_id=world["set"].id, group_id=world["group"].id,
        assigned_at=BASE, deadline=BASE + timedelta(hours=8), hard_deadline=True,
        points_per_problem=10.0, full_clear_bonus=20.0,
    ))
    await session.commit()

    anya, borya = world["anya"], world["borya"]
    p1, p2 = world["problems"]
    await _accept(session, world, anya, p1, BASE + timedelta(hours=1), "a1")
    await _accept(session, world, anya, p2, BASE + timedelta(hours=2), "a2")
    # Боря опоздал — решил после закрытия окна.
    await _accept(session, world, borya, p1, BASE + timedelta(hours=9), "b1")

    rows = await build_leaderboard(session)
    by_name = {r.user.display_name: r for r in rows}
    # 2×10 за задачи + 2×2 за first blood + 20 за полный комплект.
    assert by_name["Аня"].assignment_points == 44.0
    assert by_name["Боря"].assignment_points == 0.0


async def test_club_wide_assignment_counts_for_everyone(session, world):
    """Задание без группы — для всего клуба."""
    session.add(Assignment(title="Всем", problem_set_id=world["set"].id, assigned_at=BASE))
    await session.commit()
    await _accept(session, world, world["borya"], world["problems"][0],
                  BASE + timedelta(hours=1), "b1")

    rows = await build_leaderboard(session)
    by_name = {r.user.display_name: r for r in rows}
    assert by_name["Боря"].solved == 1
    assert by_name["Боря"].assignment_points > 0


async def test_manual_bonus_counts(session, world):
    session.add(BonusPoint(user_id=world["borya"].id, points=7.5,
                           reason="разбор на семинаре", granted_at=BASE))
    await session.commit()

    rows = await build_leaderboard(session)
    by_name = {r.user.display_name: r for r in rows}
    assert by_name["Боря"].bonus_points == 7.5
    assert rows[0].user.display_name == "Боря"


async def test_group_filter_narrows_scope(session, world):
    outsider = User(display_name="Вова")
    session.add(outsider)
    await session.commit()

    everyone = await build_leaderboard(session)
    assert len(everyone) == 3

    in_group = await build_leaderboard(session, group_id=world["group"].id)
    assert {r.user.display_name for r in in_group} == {"Аня", "Боря"}
