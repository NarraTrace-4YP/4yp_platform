"""Connecting to the project database, and keeping its tables up to date.

Table definitions live in sql/migrations/ as numbered SQL files. apply_migrations() runs
any that haven't been run yet, in order, and records each one in public.schema_migrations.
To change the database, add a new file (e.g. 002_something.sql); never edit one that has
already been merged, because the server won't run it again.
"""

import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "sql" / "migrations"


def connect(url: str | None = None) -> psycopg.Connection:
    """Connect to the project database (DHR_DATABASE_URL unless a URL is given).

    The connection is in autocommit mode: group statements that belong together inside
    `with conn.transaction():` so they all happen or none do.
    """
    return psycopg.connect(url or os.environ["DHR_DATABASE_URL"], autocommit=True)


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Run the migration files not run before, in name order. Returns the names it ran.

    Each file runs in its own transaction, so a file with an error changes nothing.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
        " name text PRIMARY KEY,"
        " applied_at timestamptz NOT NULL DEFAULT now())"
    )
    done = {row[0] for row in conn.execute("SELECT name FROM public.schema_migrations")}
    ran = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO public.schema_migrations (name) VALUES (%s)", (path.name,))
        ran.append(path.name)
    return ran
