"""SQLAlchemy 2.x declarative base.

We use the modern ``DeclarativeBase`` style with ``Mapped[...]`` annotations
on every model. Models register themselves on import.
"""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""

    pass
