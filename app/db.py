"""Database pool. Connection details come only from config (env names).

No secrets are printed or stored here.
"""

import threading
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from app import config

_pool = None
_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    """Return the shared pool, creating it lazily in a thread-safe way."""
    global _pool
    if _pool is None:
        with _lock:
            if _pool is None:
                _pool = ConnectionPool(conninfo=config.DATABASE_URL)
    return _pool


@contextmanager
def get_conn():
    """Yield one connection from the pool.

    Commits on clean exit, rolls back on exception, and always returns
    the connection to the pool. Callers in app.store commit their own
    work, so this commit is normally a no-op.
    """
    pool = get_pool()
    with pool.connection() as conn:
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()
