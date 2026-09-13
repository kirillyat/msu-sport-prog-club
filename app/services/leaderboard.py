from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Assignment,
    BonusPoint,
    Event,
    GroupMembership,
    SolveStatus,
    Submission,
    User,
)
from app.services import scoring
from app.services.progress import compute_progress, problems_for_set


@dataclass(slots=True)
class LeaderboardRow:
    user: User
    assignment_points: float = 0.0
    event_points: float = 0.0
    bonus_points: float = 0.0
    solved: int = 0
    late: int = 0
    full_clears: int = 0
    first_bloods: int = 0
    details: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return round(self.assignment_points + self.event_points + self.bonus_points, 2)


async def _users_in_scope(session: AsyncSession, group_id: int | None) -> list[User]:
    stmt = select(User).where(User.is_active.is_(True))
    if group_id is not None:
        stmt = stmt.join(GroupMembership, GroupMembership.user_id == User.id).where(
            GroupMembership.group_id == group_id
        )
    stmt = stmt.order_by(User.display_name)
    return list((await session.execute(stmt)).scalars().all())


async def _assignments_in_scope(
    session: AsyncSession, group_id: int | None, since: datetime | None
) -> list[Assignment]:
    stmt = select(Assignment)
    if group_id is not None:
        stmt = stmt.where(Assignment.group_id == group_id)
    if since is not None:
        stmt = stmt.where(Assignment.assigned_at >= since)
    return list((await session.execute(stmt)).scalars().all())


async def _event_points(
    session: AsyncSession, event: Event, user_ids: list[int]
) -> dict[int, float]:
    """Баллы за ивент: решения строго внутри временного окна."""
    problems = await problems_for_set(session, event.problem_set_id)
    if not problems or not user_ids:
        return {}
    problem_ids = [p.id for p in problems]
    stmt = (
        select(Submission.user_id, func.count(func.distinct(Submission.problem_id)))
        .where(
            Submission.is_accepted.is_(True),
            Submission.user_id.in_(user_ids),
            Submission.problem_id.in_(problem_ids),
            Submission.submitted_at >= event.starts_at,
            Submission.submitted_at <= event.ends_at,
        )
        .group_by(Submission.user_id)
    )
    out: dict[int, float] = {}
    for user_id, solved in (await session.execute(stmt)).all():
        points = solved * event.points_per_problem
        if solved == len(problems):
            points += event.full_clear_bonus
        out[user_id] = round(points, 2)
    return out


async def build_leaderboard(
    session: AsyncSession,
    *,
    group_id: int | None = None,
    since: datetime | None = None,
    include_events: bool = True,
) -> list[LeaderboardRow]:
    users = await _users_in_scope(session, group_id)
    rows = {u.id: LeaderboardRow(user=u) for u in users}
    if not rows:
        return []
    user_ids = list(rows)

    for assignment in await _assignments_in_scope(session, group_id, since):
        participants = [u for u in users if u.id in rows]
        if assignment.user_id is not None:
            participants = [u for u in participants if u.id == assignment.user_id]
        if not participants:
            continue

        progress = await compute_progress(session, assignment, participants)
        weights = {item.problem_id: item.weight for item in assignment.problem_set.items}

        for problem in progress.problems:
            solvers = [
                (u, progress.cell(u.id, problem.id))
                for u in participants
                if progress.cell(u.id, problem.id).counts
            ]
            solvers.sort(key=lambda pair: pair[1].solved_at or datetime.max)
            for index, (user, cell) in enumerate(solvers):
                row = rows[user.id]
                row.assignment_points += scoring.points_for(
                    problem, cell.status, weights.get(problem.id, 1.0)
                )
                row.solved += 1
                if cell.status == SolveStatus.solved_late:
                    row.late += 1
                # Первым решившим считаем только внутри группового задания.
                if index == 0 and len(participants) > 1:
                    row.assignment_points += scoring.FIRST_BLOOD_BONUS
                    row.first_bloods += 1

        for user in participants:
            if progress.is_complete(user.id):
                rows[user.id].assignment_points += scoring.FULL_CLEAR_BONUS
                rows[user.id].full_clears += 1

    if include_events:
        stmt = select(Event)
        if group_id is not None:
            stmt = stmt.where((Event.group_id == group_id) | (Event.group_id.is_(None)))
        if since is not None:
            stmt = stmt.where(Event.ends_at >= since)
        for event in (await session.execute(stmt)).scalars().all():
            for user_id, points in (await _event_points(session, event, user_ids)).items():
                if user_id in rows:
                    rows[user_id].event_points += points

    bonus_stmt = select(BonusPoint.user_id, func.sum(BonusPoint.points)).where(
        BonusPoint.user_id.in_(user_ids)
    )
    if since is not None:
        bonus_stmt = bonus_stmt.where(BonusPoint.granted_at >= since)
    for user_id, total in (await session.execute(bonus_stmt.group_by(BonusPoint.user_id))).all():
        if user_id in rows:
            rows[user_id].bonus_points += float(total or 0)

    for row in rows.values():
        row.assignment_points = round(row.assignment_points, 2)
        row.event_points = round(row.event_points, 2)

    return sorted(rows.values(), key=lambda r: (-r.total, r.user.display_name))
