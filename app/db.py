"""Database pool. Connection details come only from config (env names).

No secrets are printed or stored here.
"""

import threading
from contextlib import contextmanager

from psycopg_pool import ConnectionPool

from app import config

_pool = None
_pool_conninfo = None
_lock = threading.Lock()


def get_pool() -> ConnectionPool:
    """Return the shared pool, creating it lazily in a thread-safe way."""
    global _pool, _pool_conninfo
    current_url = config.DATABASE_URL
    if _pool is None or _pool_conninfo != current_url:
        with _lock:
            if _pool is None or _pool_conninfo != current_url:
                if _pool is not None:
                    try:
                        _pool.close()
                    except Exception:
                        pass
                _pool = ConnectionPool(conninfo=current_url)
                _pool_conninfo = current_url
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
