import uuid
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from app.settings import get_settings

GATEWAY_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_TABLES = {
    "tenants",
    "api_keys",
    "prices",
    "messages",
    "message_events",
    "ledger_entries",
}


def _public_tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as connection:
        return {
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }


def test_upgrade_and_downgrade_from_an_empty_database() -> None:
    base_url = make_url(get_settings().database_url)
    scratch_database = f"sms_migration_test_{uuid.uuid4().hex[:8]}"
    admin_dsn = base_url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    scratch_dsn = base_url.set(
        drivername="postgresql", database=scratch_database
    ).render_as_string(hide_password=False)
    scratch_url = base_url.set(database=scratch_database).render_as_string(
        hide_password=False
    )

    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch_database))
        )

    try:
        config = Config(str(GATEWAY_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(GATEWAY_ROOT / "migrations"))
        config.attributes["database_url"] = scratch_url

        command.upgrade(config, "head")
        assert DOMAIN_TABLES <= _public_tables(scratch_dsn)

        command.downgrade(config, "base")
        assert DOMAIN_TABLES.isdisjoint(_public_tables(scratch_dsn))
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(
                    sql.Identifier(scratch_database)
                )
            )
