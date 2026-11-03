"""Test settings. Defaults match compose.ci.yaml's published ports; env vars override."""

import os

for key, value in {
    "VIDEIO_ENV": "ci",
    "VIDEIO_JWT_SECRET": "test-only-jwt-secret-0123456789abcdef",
    "VIDEIO_DB_NAME": "videio",
    "VIDEIO_DB_USER": "videio",
    "VIDEIO_DB_PASSWORD": "change-me",
    "VIDEIO_DB_WRITE_HOST": "127.0.0.1",
    "VIDEIO_DB_WRITE_PORT": "55432",
    "VIDEIO_DB_READ_HOST": "127.0.0.1",
    "VIDEIO_DB_READ_PORT": "55433",
    "VIDEIO_REDIS_URL": "redis://127.0.0.1:56379/0",
    "VIDEIO_S3_INTERNAL_ENDPOINT_URL": "http://127.0.0.1:8333",
    "VIDEIO_S3_PUBLIC_ENDPOINT_URL": "http://127.0.0.1:8333",
    "VIDEIO_S3_ACCESS_KEY": "videio-dev-access-key",
    "VIDEIO_S3_SECRET_KEY": "change-me-s3-secret",
    "VIDEIO_APP_ORIGIN": "http://localhost:8080",
}.items():
    os.environ.setdefault(key, value)
