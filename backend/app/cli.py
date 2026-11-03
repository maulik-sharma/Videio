"""Operator commands, run inside the api container:

python -m app.cli promote-admin someone@example.com
"""

import argparse
import asyncio
import sys

from sqlalchemy import update

from app.core.config import get_settings
from app.db.engines import Database
from app.db.models import User


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    promote = commands.add_parser("promote-admin", help="Make an existing account an admin")
    promote.add_argument("email")
    args = parser.parse_args(argv)

    if args.command == "promote-admin":
        if not asyncio.run(promote_admin(args.email)):
            print(f"No account with email {args.email}. Register it first.", file=sys.stderr)
            return 1
        print(f"{args.email} is now an admin. It takes effect on their next request.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
