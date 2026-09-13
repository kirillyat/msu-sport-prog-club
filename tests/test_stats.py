from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.models import Platform, PlatformAccount, Problem, Submission, User
from app.services.stats import build_heatmap, user_stats


def test_heatmap_columns_are_calendar_weeks():
    heatmap = build_heatmap({}, weeks=4)
    assert len(heatmap.weeks) == 4
    for column in heatmap.weeks:
        assert len(column) == 7
        # Столбец — календарная неделя: сверху понедельник, снизу воскресенье.
        assert column[0].day.weekday() == 0
        assert column[-1].day.weekday() == 6
        for earlier, later in zip(column, column[1:], strict=False):
            assert (later.day - earlier.day).days == 1


def test_heatmap_ends_on_current_week():
    heatmap = build_heatmap({}, weeks=4)
    last_week = [d.day for d in heatmap.weeks[-1]]
    assert date.today() in last_week


def test_future_days_are_marked_and_not_counted():
    heatmap = build_heatmap({date.today(): 3}, weeks=2)
    future = [d for column in heatmap.weeks for d in column if d.future]
    assert all(d.day > date.today() for d in future)
    assert all(d.level == 0 for d in future)
    assert heatmap.total == 3
    assert heatmap.active_days == 1


def test_heatmap_levels_scale_with_count():
    today = date.today()
    counts = {today: 1, today - timedelta(days=1): 2,
              today - timedelta(days=2): 5, today - timedelta(days=3): 20}
    heatmap = build_heatmap(counts, weeks=3)
    levels = {d.day: d.level for column in heatmap.weeks for d in column}
    assert levels[today] == 1
    assert levels[today - timedelta(days=1)] == 2
    assert levels[today - timedelta(days=2)] == 3
    assert levels[today - timedelta(days=3)] == 4
    assert levels[today - timedelta(days=10)] == 0


def test_month_labels_point_at_real_weeks():
    heatmap = build_heatmap({}, weeks=12)
    assert heatmap.month_labels, "подписи месяцев должны быть"
    for week_index in heatmap.month_labels:
        assert 0 <= week_index < len(heatmap.weeks)


async def test_user_stats_counts_unique_problems(session):
    user = User(display_name="Аня")
    session.add(user)
    await session.commit()

    problems = [
        Problem(platform=Platform.leetcode, external_id="1", slug="two-sum", title="Two Sum",
                url="", difficulty="Easy", tags=["Array", "Hash Table"]),
        Problem(platform=Platform.leetcode, external_id="2", slug="add-two", title="Add Two",
                url="", difficulty="Hard", tags=["Array"]),
    ]
    session.add_all(problems)
    await session.commit()

    account = PlatformAccount(user_id=user.id, platform=Platform.leetcode,
                              handle="anya", verified_at=datetime.now(UTC))
    session.add(account)
    await session.commit()

    when = datetime.now(UTC) - timedelta(days=1)
    for index, problem in enumerate(problems):
        session.add(Submission(
            user_id=user.id, platform_account_id=account.id, platform=Platform.leetcode,
            external_id=f"s{index}", problem_id=problem.id, problem_slug=problem.slug,
            verdict="Accepted", is_accepted=True, submitted_at=when,
        ))
    # Повторное решение той же задачи не должно увеличивать «уникальные».
    session.add(Submission(
        user_id=user.id, platform_account_id=account.id, platform=Platform.leetcode,
        external_id="dup", problem_id=problems[0].id, problem_slug=problems[0].slug,
        verdict="Accepted", is_accepted=True, submitted_at=when,
    ))
    await session.commit()

    stats = await user_stats(session, user)
    assert stats.unique_problems == 2
    assert stats.total_accepted == 3
    assert stats.by_difficulty == {"Easy": 1, "Hard": 1}
    assert stats.by_platform == {"LeetCode": 2}
    assert dict(stats.top_tags)["Array"] == 2
    assert stats.max_difficulty == 1
    assert stats.heatmap.total == 3
