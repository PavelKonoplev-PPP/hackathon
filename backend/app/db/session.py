from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import get_settings

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


def check_db() -> bool:
    """Check connectivity to PostgreSQL database by executing a probe query."""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
