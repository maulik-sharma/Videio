"""Operator commands, run inside the api container:

python -m app.cli promote-admin someone@example.com
python -m app.cli retranscode 3kTq9bZx1A2
"""

import argparse
import asyncio
import sys

from sqlalchemy import select, update

from app.core.config import get_settings
from app.db.engines import Database
from app.db.models import User, Video


async def promote_admin(email: str) -> bool:
    """Give an existing account the admin role. Returns False if no such account."""
    db = Database.from_settings(get_settings())
    try:
        async with db.write_sessions() as session:
            result = await session.execute(
                update(User).where(User.email == email).values(role="admin").returning(User.id)
            )
            await session.commit()
            return result.first() is not None
    finally:
        await db.dispose()


async def retranscode(public_id: str) -> str | None:
    """Queue a failed or stuck video for another transcode. Returns an error or None."""
    from app.worker.broker import broker
    from app.worker.tasks import transcode_video

    db = Database.from_settings(get_settings())
    try:
        async with db.write_sessions() as session:
            video = await session.scalar(select(Video).where(Video.public_id == public_id))
            if video is None:
                return f"No video with id {public_id}."
            if video.status not in {"failed", "uploaded", "processing"}:
                return f"Video {public_id} is {video.status}; only failed or stuck videos re-run."
            video.status = "uploaded"
            video.failure_reason = None
            await session.commit()
            video_id = video.id
    finally:
        await db.dispose()

    await broker.startup()
    try:
        await transcode_video.kiq(video_id)
    finally:
        await broker.shutdown()
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    promote = commands.add_parser("promote-admin", help="Make an existing account an admin")
    promote.add_argument("email")
    rerun = commands.add_parser("retranscode", help="Queue a failed video for another transcode")
    rerun.add_argument("public_id")
    args = parser.parse_args(argv)

    if args.command == "promote-admin":
        if not asyncio.run(promote_admin(args.email)):
            print(f"No account with email {args.email}. Register it first.", file=sys.stderr)
            return 1
        print(f"{args.email} is now an admin. It takes effect on their next request.")
    elif args.command == "retranscode":
        error = asyncio.run(retranscode(args.public_id))
        if error:
            print(error, file=sys.stderr)
            return 1
        print(f"Queued {args.public_id} for transcoding.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
