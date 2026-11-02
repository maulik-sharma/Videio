from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from environment variables (and `.env` in local dev)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="VIDEIO_", extra="ignore")

    env: Literal["dev", "ci", "prod"] = "dev"
    # Image build identifier (e.g. "sha-1a2b3c4"); baked in at build time so a
    # deploy smoke check can confirm which version is actually serving traffic.
    version: str = "dev"


@lru_cache
def get_settings() -> Settings:
    return Settings()
