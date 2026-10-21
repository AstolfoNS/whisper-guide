"""SQLite connection setup; business tables will be added with their features."""

from sqlalchemy import Engine, create_engine, text

from app.config import Settings


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
