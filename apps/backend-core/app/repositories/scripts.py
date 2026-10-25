"""SQLite persistence operations for script records.

The API layer owns request validation and response shaping.  This module keeps
SQLAlchemy statements and transaction boundaries in one place.
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.script import Script


def list_scripts(session: Session) -> list[Script]:
    statement = select(Script).order_by(Script.created_at.desc(), Script.id)
    return list(session.scalars(statement))


def get_script(session: Session, script_id: str) -> Script | None:
    return session.get(Script, script_id)


def add_scripts(session: Session, scripts: Sequence[Script]) -> list[Script]:
    session.add_all(scripts)
    session.commit()
    for script in scripts:
        session.refresh(script)
    return list(scripts)


def delete_script(session: Session, script: Script) -> None:
    session.delete(script)
    session.commit()
