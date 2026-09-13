from __future__ import annotations

import pytest

from app.models import Platform, Problem
from app.services.problem_parser import parse_problem_list, search_problems


@pytest.fixture
async def catalog(session):
    problems = [
        Problem(platform=Platform.leetcode, external_id="1", slug="two-sum",
                title="Two Sum", url="https://leetcode.com/problems/two-sum/",
                difficulty="Easy", tags=["Array"]),
        Problem(platform=Platform.leetcode, external_id="704", slug="binary-search",
                title="Binary Search", url="https://leetcode.com/problems/binary-search/",
                difficulty="Easy", tags=["Binary Search"]),
        Problem(platform=Platform.codeforces, external_id="4A", slug="4a",
                title="Watermelon", url="https://codeforces.com/problemset/problem/4/A",
                rating=800, tags=["math"]),
        Problem(platform=Platform.codeforces, external_id="1352A", slug="1352a",
                title="Sum of Round Numbers",
                url="https://codeforces.com/problemset/problem/1352/A", rating=800),
    ]
    session.add_all(problems)
    await session.commit()
    return problems


async def test_parses_every_supported_form(session, catalog):
    text = """
    https://leetcode.com/problems/two-sum/
    lc:binary-search
    binary-search
    https://codeforces.com/problemset/problem/4/A
    cf:1352A
    4A
    """
    result = await parse_problem_list(session, text)
    assert [p.external_id for p in result.problems] == ["1", "704", "4A", "1352A"]
    assert result.unresolved == []


async def test_reports_unknown_lines(session, catalog):
    result = await parse_problem_list(session, "two-sum\nтакой-задачи-нет\n9999Z")
    assert [p.external_id for p in result.problems] == ["1"]
    assert result.unresolved == ["такой-задачи-нет", "9999Z"]


async def test_contest_url_form(session, catalog):
    result = await parse_problem_list(session, "https://codeforces.com/contest/1352/problem/A")
    assert [p.external_id for p in result.problems] == ["1352A"]


async def test_search_filters_by_platform(session, catalog):
    found = await search_problems(session, "sum", Platform.leetcode)
    assert [p.title for p in found] == ["Two Sum"]
