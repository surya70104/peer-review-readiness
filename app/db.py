import os
from contextlib import contextmanager
from pathlib import Path

from dotenv import load_dotenv
from psycopg2.pool import ThreadedConnectionPool

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pool: ThreadedConnectionPool | None = None


def init_pool() -> None:
    """Create the pool. Raises if the database is unreachable."""
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")
    if _pool is None:
        _pool = ThreadedConnectionPool(1, 10, DATABASE_URL)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.closeall()
        _pool = None


@contextmanager
def get_conn(commit: bool = True):
    """Yield a pooled connection; commit on success (or roll back when
    commit=False, used for dry runs), roll back on error."""
    if _pool is None:
        init_pool()
    conn = _pool.getconn()
    try:
        yield conn
        if commit:
            conn.commit()
        else:
            conn.rollback()
    except Exception:
        conn.rollback()
        raise
    finally:
        _pool.putconn(conn)
