"""Operator commands, run inside the api or worker container:

python -m app.cli promote-admin someone@example.com
python -m app.cli retranscode 3kTq9bZx1A2
python -m app.cli seed
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import func, select, update

from app.core.config import get_settings
from app.core.ids import new_public_id
from app.core.security import hash_password
from app.db.engines import Database
from app.db.models import User, Video
from app.services.storage import S3Storage

# ---------------------------------------------------------------------------
# Demo seed data
# ---------------------------------------------------------------------------

DEMO_ADMIN_EMAIL = "admin@videio.local"
DEMO_ADMIN_PASSWORD = "demo-admin"
DEMO_USER_EMAIL = "user@videio.local"
DEMO_USER_PASSWORD = "demo-user"

# (title, description, visibility, ffmpeg lavfi video filter)
_SAMPLE_VIDEOS: list[tuple[str, str, str, str]] = [
    (
        "SMPTE Colour Bars",
        "Classic SMPTE colour bars generated with ffmpeg lavfi. "
        "Great for checking colour reproduction in the HLS player.",
        "public",
        "smptebars=duration=8:size=1280x720:rate=25",
    ),
    (
        "Test Pattern",
        "A synthetic test pattern at 1080p. "
        "Demonstrates the full HLS ladder (144p → 1080p) and the quality selector.",
        "public",
        "testsrc2=duration=8:size=1920x1080:rate=25",
    ),
    (
        "Mandelbrot Zoom (unlisted)",
        "A mandelbrot fractal clip. This video is unlisted — it does not appear in the "
        "public feed but is playable by anyone with the link.",
        "unlisted",
        "mandelbrot=duration=8:size=854x480:rate=25",
    ),
]


# ---------------------------------------------------------------------------
# promote-admin
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# retranscode
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# seed
# ---------------------------------------------------------------------------


async def _ensure_user(
    db: Database,
    email: str,
    password: str,
    display_name: str,
    *,
    role: str = "user",
) -> User:
    """Return the user, creating it if it does not yet exist."""
    async with db.write_sessions() as session:
        existing = await session.scalar(select(User).where(User.email == email))
        if existing is not None:
            return existing
        user = User(
            email=email,
            password_hash=hash_password(password),
            display_name=display_name,
            role=role,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        print(f"  created {role}: {email}  (password: {password})")
        return user


async def _already_seeded(db: Database, owner_id: object) -> bool:
    """True when the demo user already has at least one ready video."""
    async with db.read_sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Video)
            .where(Video.owner_id == owner_id, Video.status == "ready")  # type: ignore[arg-type]
        )
        return bool(count and count > 0)


async def _generate_clip(lavfi_filter: str, dest: Path) -> None:
    """Generate a short MP4 test clip with ffmpeg lavfi (requires the worker image)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-f", "lavfi", "-i", lavfi_filter,
        "-f", "lavfi", "-i", "sine=frequency=440:duration=8:sample_rate=44100",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest",
        str(dest),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed (exit {proc.returncode}): {stderr.decode()[-500:]}"
        )


async def _seed_video(
    db: Database,
    storage: S3Storage,
    owner: User,
    title: str,
    description: str,
    visibility: str,
    lavfi_filter: str,
) -> None:
    """Generate one lavfi clip, transcode it, upload HLS outputs, insert a ready row."""
    # Inline import: transcode modules live in app.worker, available in the worker image.
    from app.worker.ffmpeg import probe_media, transcode_hls

    public_id = new_public_id()
    original_key = f"originals/{public_id}"
    media_prefix = f"videos/{public_id}"

    with TemporaryDirectory(prefix=f"videio-seed-{public_id}-") as tmp:
        source = Path(tmp) / "original.mp4"
        workdir = Path(tmp) / "hls"

        print(f"  generating '{title}' ({lavfi_filter[:30]}…)")
        await _generate_clip(lavfi_filter, source)

        # Upload original to the uploads bucket so the row is consistent.
        await storage.upload_original_from_path(source, original_key, "video/mp4")
        size_bytes = source.stat().st_size

        # Transcode inline — no queue needed.
        print(f"  transcoding '{title}'…")
        probe = await probe_media(source)
        master, thumbnail, renditions = await transcode_hls(source, workdir, probe)

        # Upload HLS outputs (master last so it never points at missing segments).
        def _ctype(path: Path) -> str:
            return {
                ".m3u8": "application/vnd.apple.mpegurl",
                ".ts": "video/mp2t",
                ".jpg": "image/jpeg",
            }.get(path.suffix, "application/octet-stream")

        for output in sorted(workdir.rglob("*")):
            if output.is_file() and output != master:
                await storage.upload_media_file(
                    output,
                    f"{media_prefix}/{output.relative_to(workdir).as_posix()}",
                    _ctype(output),
                )
        await storage.upload_media_file(master, f"{media_prefix}/{master.name}", _ctype(master))

        rendition_data: dict[str, object] = {
            "master": f"{media_prefix}/{master.name}",
            "source": {"width": probe.width, "height": probe.height},
            "ladder": [
                {
                    "name": r.name,
                    "width": r.width,
                    "height": r.height,
                    "bitrate_kbps": r.bitrate_kbps,
                    "playlist": f"{media_prefix}/{r.name}/index.m3u8",
                }
                for r in renditions
            ],
        }

        async with db.write_sessions() as session:
            video = Video(
                public_id=public_id,
                owner_id=owner.id,
                title=title,
                description=description,
                visibility=visibility,
                status="ready",
                original_key=original_key,
                original_size_bytes=size_bytes,
                original_content_type="video/mp4",
                duration_s=probe.duration_s,
                renditions=rendition_data,
                thumbnail_key=f"{media_prefix}/{thumbnail.name}",
            )
            session.add(video)
            await session.commit()
        print(f"  ready: '{title}' → /v/{public_id}")


async def seed() -> int:
    """Create demo accounts and seed sample public videos.

    Idempotent: accounts are created only when they do not exist; videos are
    seeded only when the demo user has no ready videos yet.

    Requires ffmpeg (available in the worker image). Run via:
      docker compose run --rm seed
    """
    settings = get_settings()
    db = Database.from_settings(settings)
    storage = S3Storage.from_settings(settings)

    try:
        print("Seeding demo accounts…")
        await _ensure_user(db, DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, "Demo Admin", role="admin")
        demo_user = await _ensure_user(db, DEMO_USER_EMAIL, DEMO_USER_PASSWORD, "Demo User")

        if await _already_seeded(db, demo_user.id):
            print("Demo videos already present; skipping video generation.")
            return 0

        print("Seeding sample videos (this takes a minute — ffmpeg is running)…")
        for title, description, visibility, lavfi_filter in _SAMPLE_VIDEOS:
            await _seed_video(db, storage, demo_user, title, description, visibility, lavfi_filter)

        print("Done. Open http://localhost:8080 to see the seeded feed.")
        return 0
    except Exception as exc:
        print(f"Seed failed: {exc}", file=sys.stderr)
        return 1
    finally:
        await db.dispose()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    promote = commands.add_parser("promote-admin", help="Make an existing account an admin")
    promote.add_argument("email")

    rerun = commands.add_parser("retranscode", help="Queue a failed video for another transcode")
    rerun.add_argument("public_id")

    commands.add_parser(
        "seed",
        help="Create demo accounts and seed sample videos (requires ffmpeg / worker image)",
    )

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
    elif args.command == "seed":
        return asyncio.run(seed())
    return 0


if __name__ == "__main__":
    sys.exit(main())
