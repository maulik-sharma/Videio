from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read from VIDEIO_* environment variables (and `.env` in dev)."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="VIDEIO_", extra="ignore")

    env: Literal["dev", "ci", "prod"] = "dev"
    # Image build identifier (e.g. "sha-1a2b3c4"); baked in at build time so a
    # deploy smoke check can confirm which version is actually serving traffic.
    version: str = "dev"

    # Core database: writes go to the primary, most reads to the streaming replica.
    db_name: str = "videio"
    db_user: str = "videio"
    db_password: SecretStr = SecretStr("")
    db_write_host: str = "core-primary"
    db_write_port: int = 5432
    db_read_host: str = "core-replica"
    db_read_port: int = 5432
    db_pool_size: int = 5

    redis_url: str = "redis://redis:6379/0"

    # RabbitMQ task broker. The API publishes jobs and workers/scheduler consume them.
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    # Management HTTP API, for queue depths on the admin System panel. When unset,
    # it is the broker's host on port 15672 with the broker's credentials.
    rabbitmq_management_url: str | None = None

    # S3-compatible object storage. The API uses the internal endpoint for
    # server-side operations and the public endpoint when signing browser URLs.
    s3_internal_endpoint_url: str = "http://localhost:8333"
    s3_public_endpoint_url: str = "http://localhost:8333"
    s3_access_key: SecretStr = SecretStr("videio-dev-access-key")
    s3_secret_key: SecretStr = SecretStr("change-me-s3-secret")
    s3_region: str = "us-east-1"
    s3_upload_bucket: str = "uploads"
    s3_media_bucket: str = "media"
    s3_part_size_bytes: int = Field(default=10 * 1024 * 1024, gt=0)
    s3_presign_ttl_s: int = Field(default=60 * 60, gt=0)
    app_origin: str = "http://localhost:8080"

    # Auth. The JWT secret signs access tokens; at least 32 characters.
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 7 * 24 * 60 * 60
    # Secure cookies need HTTPS; local dev over http://localhost:8080 turns this off.
    cookie_secure: bool = True

    # Read-your-writes: after a user writes, their reads use the primary this long.
    rw_flag_ttl_s: int = 5

    # Rate limits (token buckets): N requests per window.
    register_limit_per_ip: int = 5
    register_window_s: int = 60
    login_limit_per_email: int = 5
    login_window_s: int = 60

    # Upload abuse protection. The limit is deliberately per account so a
    # logged-in user cannot exhaust every API replica independently.
    upload_limit_per_user: int = 10
    upload_window_s: int = 60 * 60
    max_upload_size_bytes: int = 500_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()  # jwt_secret has no default: it must come from the environment
