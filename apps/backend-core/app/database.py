"""SQLite connections and short-lived database sessions."""

from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import Settings


class Base(DeclarativeBase):
    pass


def create_database_engine(settings: Settings) -> Engine:
    settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        engine.dispose()
        raise
    return engine


def get_database_session(request: Request) -> Iterator[Session]:
    with Session(request.app.state.database_engine) as session:
        yield session
