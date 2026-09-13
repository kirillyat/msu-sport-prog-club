from __future__ import annotations

from fastapi import APIRouter, Request

from app.deps import CurrentUser, OptionalInt, SessionDep
from app.services.feed import build_feed, groups_for_feed
from app.templating import templates

router = APIRouter(tags=["feed"])


@router.get("/feed")
async def feed_page(
    request: Request, session: SessionDep, user: CurrentUser, group_id: OptionalInt = None
):
    groups = await groups_for_feed(session, user)
    if group_id is not None and all(g.id != group_id for g in groups):
        group_id = None

    items = await build_feed(session, user, group_id=group_id)
    return templates.TemplateResponse(
        request,
        "feed.html",
        {"user": user, "items": items, "groups": groups, "group_id": group_id},
    )
