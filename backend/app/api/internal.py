"""Endpoints only the edge calls. nginx marks their location `internal`, so a
browser can never reach them directly."""

from fastapi import APIRouter, Query, Response

from app.api.deps import OptionalActor, ReadSession, RedisDep
from app.core.policy import can
from app.services import cache

router = APIRouter(prefix="/internal", include_in_schema=False)


@router.get("/authz")
async def media_authz(
    session: ReadSession,
    redis: RedisDep,
    actor: OptionalActor,
    video: str = Query(default=""),
) -> Response:
    """nginx `auth_request` for /media/{publicId}/...: 204 to serve, 403 to refuse.

    The browser's session cookie rides along on every playlist and segment
    request, so this answers from the cached video record and the signed access
    token without touching Postgres. nginx accepts only 2xx, 401 and 403 here,
    so a video that does not exist is a 403 too.

    `X-Media-Cacheable` tells the edge whether it may keep a copy: public and
    unlisted segments are cached, private ones always come from storage.
    """
    if not cache.is_public_id(video):
        return Response(status_code=403)
    record, _ = await cache.get_video_record(redis, session, video)
    if record is None or not can(actor, "view", record):
        return Response(status_code=403)
    cacheable = "1" if record.visibility != "private" else "0"
    return Response(status_code=204, headers={"X-Media-Cacheable": cacheable})
