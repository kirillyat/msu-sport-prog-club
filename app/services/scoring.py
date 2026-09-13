"""Правила начисления баллов.

Держим формулу в одном месте: её точно захочется крутить по ходу семестра,
и это не должно требовать миграций.
"""

from __future__ import annotations

from app.models import Platform, Problem, SolveStatus

# LeetCode: базовая стоимость по сложности.
LEETCODE_BASE = {"easy": 1.0, "medium": 3.0, "hard": 5.0}
LEETCODE_FALLBACK = 2.0

# Codeforces: задача 800 → 4 балла, 1600 → 8, 2400 → 12.
CODEFORCES_DIVISOR = 200.0
CODEFORCES_FALLBACK = 3.0

# Решено после дедлайна — половина. Решать всё равно выгоднее, чем забить.
LATE_MULTIPLIER = 0.5
FIRST_BLOOD_BONUS = 2.0
FULL_CLEAR_BONUS = 5.0


def problem_points(problem: Problem) -> float:
    if problem.platform == Platform.leetcode:
        key = (problem.difficulty or "").lower()
        return LEETCODE_BASE.get(key, LEETCODE_FALLBACK)
    if problem.rating:
        return round(problem.rating / CODEFORCES_DIVISOR, 1)
    return CODEFORCES_FALLBACK


def status_multiplier(status: SolveStatus) -> float:
    if status == SolveStatus.solved_in_time:
        return 1.0
    if status == SolveStatus.solved_late:
        return LATE_MULTIPLIER
    return 0.0


def points_for(problem: Problem, status: SolveStatus, weight: float = 1.0) -> float:
    return round(problem_points(problem) * status_multiplier(status) * weight, 2)
