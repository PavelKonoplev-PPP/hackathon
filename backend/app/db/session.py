from collections.abc import Iterator

import psycopg
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from ..config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return SQLAlchemy engine singleton, creating it on first call."""
    global _engine
    if _engine is None:
        _engine = create_engine(get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_db() -> Iterator[Session]:
    """Provide a transactional database session generator."""
    with Session(get_engine()) as session:
        yield session


def get_reader_connection() -> psycopg.Connection:
    """Create a new read-only psycopg connection under the bi_reader role."""
    settings = get_settings()
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_reader_user,
        password=settings.reader_password,
        connect_timeout=10,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    )


def check_db() -> bool:
    """Check connectivity to PostgreSQL database by executing a probe query."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
