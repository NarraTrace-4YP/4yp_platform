"""Connecting to the project database, and keeping its tables up to date.

The tables are defined in migrations.py. apply_migrations() runs any not run before, in
order, and records each one in public.schema_migrations. Every pipeline calls it first,
so a new migration takes effect the next time any pipeline runs.
"""

import psycopg

from .migrations import MIGRATIONS
from .settings import database_url


def connect(url: str | None = None) -> psycopg.Connection:
    """Connect to the project database (in Windmill, the f/dhr/database_url secret).

    The connection is in autocommit mode: group statements that belong together inside
    `with conn.transaction():` so they all happen or none do.
    """
    return psycopg.connect(url or database_url(), autocommit=True)


def apply_migrations(
    conn: psycopg.Connection, migrations: list[tuple[str, str]] = MIGRATIONS
) -> list[str]:
    """Run the migrations not run before, in order. Returns the names it ran.

    Each one runs in its own transaction, so a migration with an error changes nothing.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
        " name text PRIMARY KEY,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {row[0] for row in conn.execute("SELECT name FROM public.schema_migrations")}
    ran = []
    for name, sql in migrations:
        if name in done:
            continue
        with conn.transaction():
            conn.execute(sql)
            conn.execute("INSERT INTO public.schema_migrations (name) VALUES (%s)", (name,))
        ran.append(name)
    return ran
