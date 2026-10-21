"""Database engine and session dependency."""

from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from acquisition.config import get_settings

settings = get_settings()

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)


@event.listens_for(engine, "checkin")
def _clear_advisory_locks_on_checkin(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Prevent session-level advisory locks from sticking on pooled connections."""
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute("SELECT pg_advisory_unlock_all()")
        cursor.close()
        # Ensure unlock is visible; psycopg3 may be in autocommit or not.
        if hasattr(dbapi_connection, "commit"):
            dbapi_connection.commit()
    except Exception:  # noqa: BLE001 — never block pool return
        try:
            dbapi_connection.rollback()
        except Exception:  # noqa: BLE001
            pass


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database_connection() -> bool:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return True
