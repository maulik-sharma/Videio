"""One-shot migration entrypoint: `python -m app.db.migrate` (alembic upgrade head)."""

from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[2]


def alembic_config() -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    return config


def main() -> None:
    command.upgrade(alembic_config(), "head")


if __name__ == "__main__":
    main()
