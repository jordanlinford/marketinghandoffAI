"""Patched for two-process SQLite (API + worker). WAL + busy_timeout so one
writer + many readers coexist without 'database is locked' errors."""
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

if settings.is_sqlite:
    _connect_args = {"check_same_thread": False, "timeout": 30}
    _db_url = settings.database_url
else:
    _connect_args = {}
    # Force psycopg v3 driver (we don't install psycopg2). Replit's DATABASE_URL
    # ships as plain "postgresql://...", which SQLAlchemy maps to psycopg2.
    _db_url = settings.database_url
    if _db_url.startswith("postgres://"):
        _db_url = "postgresql+psycopg://" + _db_url[len("postgres://"):]
    elif _db_url.startswith("postgresql://"):
        _db_url = "postgresql+psycopg://" + _db_url[len("postgresql://"):]

engine = create_engine(_db_url, connect_args=_connect_args, future=True)

if settings.is_sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    # Dev convenience. In prod, run sql/schema.sql (it also installs RLS policies).
    from app import models  # noqa: F401  (ensure models are imported/registered)

    Base.metadata.create_all(bind=engine)
