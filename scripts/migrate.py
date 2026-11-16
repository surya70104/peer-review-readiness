"""Migration runner.

1. Reads DATABASE_URL from .env.
2. Connects to the default `postgres` maintenance DB and runs
   CREATE DATABASE <name> if it does not exist (no psql needed).
3. Applies migrations/*.sql in filename order, each in its own transaction,
   tracked in the schema_migrations table (with a checksum so an edited,
   already-applied migration is flagged instead of silently ignored).
"""
import hashlib
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"


def get_url() -> str:
    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set (check .env)")
    return url


def ensure_database(url: str) -> None:
    parsed = urlparse(url)
    dbname = parsed.path.lstrip("/")
    admin_url = urlunparse(parsed._replace(path="/postgres"))
    conn = psycopg2.connect(admin_url)
    conn.autocommit = True  # CREATE DATABASE cannot run inside a transaction
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,))
            if cur.fetchone():
                print(f"[db] database '{dbname}' exists")
            else:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
                print(f"[db] created database '{dbname}'")
    finally:
        conn.close()


def run_migrations(url: str) -> None:
    conn = psycopg2.connect(url)
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename   TEXT PRIMARY KEY,
                    checksum   TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        with conn.cursor() as cur:
            cur.execute("SELECT filename, checksum FROM schema_migrations")
            applied = dict(cur.fetchall())
        conn.commit()

        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        if not files:
            print("[migrate] no migration files found")
        for f in files:
            body = f.read_text(encoding="utf-8")
            checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
            if f.name in applied:
                if applied[f.name] != checksum:
                    sys.exit(
                        f"[migrate] ERROR: {f.name} was already applied but has changed on disk. "
                        "Never edit an applied migration; add a new one."
                    )
                print(f"[migrate] skip   {f.name}")
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(body)
                    cur.execute(
                        "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                        (f.name, checksum),
                    )
                conn.commit()
            except Exception as exc:
                conn.rollback()
                sys.exit(f"[migrate] FAILED {f.name}: {exc}")
            print(f"[migrate] applied {f.name}")
    finally:
        conn.close()


def main() -> None:
    url = get_url()
    ensure_database(url)
    run_migrations(url)
    print("[migrate] done")


if __name__ == "__main__":
    main()
