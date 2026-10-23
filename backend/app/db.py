from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./shiftsync.db")
# Hosted Postgres providers commonly return postgresql:// URLs. Force SQLAlchemy
# to use psycopg 3, which is the driver installed by this project.
if DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    employee_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    default_title: Mapped[str] = mapped_column(String(160), default="Work Shift", nullable=False)
    default_location: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), default="Europe/Dublin", nullable=False)
    google_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
