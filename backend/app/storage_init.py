"""Initialise SeaweedFS buckets before the API starts serving traffic."""

import asyncio

from app.core.config import get_settings
from app.services.storage import S3Storage


async def initialise() -> None:
    await S3Storage.from_settings(get_settings()).ensure_buckets()


def main() -> None:
    asyncio.run(initialise())


if __name__ == "__main__":
    main()
