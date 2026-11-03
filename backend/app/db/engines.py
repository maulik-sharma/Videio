"""Two async engines: `write` (primary) and `read` (streaming replica)."""

from dataclasses import dataclass

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def database_url(settings: Settings, *, host: str, port: int) -> URL:
    # URL.create escapes the password, so any characters are safe in it.
    return URL.create(
        "postgresql+asyncpg",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=host,
        port=port,
        database=settings.db_name,
    )


@dataclass
class Database:
    write_engine: AsyncEngine
    read_engine: AsyncEngine
    write_sessions: async_sessionmaker[AsyncSession]
    read_sessions: async_sessionmaker[AsyncSession]

    @classmethod
    def from_settings(cls, settings: Settings) -> "Database":
        def engine(host: str, port: int) -> AsyncEngine:
            return create_async_engine(
                database_url(settings, host=host, port=port),
                pool_size=settings.db_pool_size,
                pool_pre_ping=True,
            )

        write = engine(settings.db_write_host, settings.db_write_port)
        read = engine(settings.db_read_host, settings.db_read_port)
        return cls(
            write_engine=write,
            read_engine=read,
            write_sessions=async_sessionmaker(write, expire_on_commit=False),
            read_sessions=async_sessionmaker(read, expire_on_commit=False),
        )

    async def dispose(self) -> None:
        await self.write_engine.dispose()
        await self.read_engine.dispose()
