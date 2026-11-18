from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]


def test_migrations_apply_and_match_models(tmp_path):
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = f"sqlite:///{tmp_path / 'migrations.db'}"

    command.upgrade(config, "head")
    command.check(config)  # падает, если модели разошлись с миграциями
    command.downgrade(config, "base")
    command.upgrade(config, "head")  # и обратно, чтобы downgrade тоже был рабочим
