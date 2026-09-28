"""The Alembic migration must produce the same schema as the models."""

import os

from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from alembic import command
from trustmebank.db import Base


def test_alembic_upgrade_matches_models():
    url = os.environ["TEST_DATABASE_URL"].replace("trustmebank_test", "trustmebank_test_migrations")
    admin = create_engine(os.environ["TEST_DATABASE_URL"], isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS trustmebank_test_migrations"))
        conn.execute(text("CREATE DATABASE trustmebank_test_migrations"))
    admin.dispose()

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    from trustmebank.config import get_settings

    get_settings.cache_clear()
    try:
        command.upgrade(cfg, "head")
        engine = create_engine(url)
        migrated = inspect(engine)
        migrated_tables = set(migrated.get_table_names()) - {"alembic_version"}
        assert migrated_tables == set(Base.metadata.tables)
        for table in Base.metadata.tables.values():
            cols = {c["name"] for c in migrated.get_columns(table.name)}
            assert cols == {c.name for c in table.columns}, table.name
        command.downgrade(cfg, "base")
        assert set(inspect(engine).get_table_names()) - {"alembic_version"} == set()
        engine.dispose()
    finally:
        os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
