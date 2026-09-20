"""Job, OutboxEvent — dev-plan §4."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (CheckConstraint("status IN ('PENDING','DONE')", name="ck_jobs_status"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    request_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING','SENT')", name="ck_outbox_status"),
        Index("ix_outbox_status_next_attempt", "status", "next_attempt_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 업무당 이벤트 하나. 재발행 버전이 필요하면 UNIQUE 해제 + version (§13-8)
    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jobs.id"), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False, default="COMPUTE_JOB")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
