"""Database schema.

ponytail: the spec's 16 tables are collapsed to 10 — departments are a string on
Doctor, clinic==Branch, conversation state is a JSON column on CallSession,
prompt versions live in prompts/ (git-versioned files), errors go to JSON logs.
Split them out when a real reporting need appears.
"""
from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def local_now() -> datetime:
    from packages.shared.clock import now  # late import; avoids settings at import time
    return now()


class Base(DeclarativeBase):
    pass


class Branch(Base):
    __tablename__ = "branches"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(32))
    # {"mon": "09:00-17:00", ...} plus faq facts like insurance/pricing text
    info: Mapped[dict] = mapped_column(JSON, default=dict)


class Doctor(Base):
    __tablename__ = "doctors"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    department: Mapped[str] = mapped_column(String(80), index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"))
    branch: Mapped[Branch] = relationship()
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="doctor")


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(primary_key=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0=Monday
    start: Mapped[time] = mapped_column(Time)
    end: Mapped[time] = mapped_column(Time)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=0)  # required gap between appointments
    doctor: Mapped[Doctor] = relationship(back_populates="schedules")
    __table_args__ = (UniqueConstraint("doctor_id", "weekday", name="uq_schedule_day"),)


class Patient(Base):
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    phone: Mapped[str] = mapped_column(String(20), index=True)  # NOT unique: family lines share numbers
    dob: Mapped[date] = mapped_column(Date)
    insurance: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cliniko_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # PMS patient id
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=local_now)


class Appointment(Base):
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    branch_id: Mapped[int] = mapped_column(ForeignKey("branches.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(), index=True)
    status: Mapped[str] = mapped_column(String(16), default="booked")  # booked|cancelled|done
    pms_status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|synced|failed
    cliniko_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # PMS appt id
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=local_now)
    patient: Mapped[Patient] = relationship()
    doctor: Mapped[Doctor] = relationship()
    # DB-level double-booking guard: only *booked* rows conflict, so a slot can
    # be re-booked after a cancellation. App catches IntegrityError and re-offers.
    __table_args__ = (
        Index(
            "uq_doctor_slot", "doctor_id", "starts_at",
            unique=True,
            postgresql_where=text("status = 'booked'"),
            sqlite_where=text("status = 'booked'"),
        ),
    )


class CallSession(Base):
    __tablename__ = "call_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    sid: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # telephony call id
    caller: Mapped[str] = mapped_column(String(32))
    language: Mapped[str] = mapped_column(String(8), default="en")
    state: Mapped[str] = mapped_column(String(32), default="greeting")
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # slots, intent, retries, summary
    outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(), default=local_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    messages: Mapped[list["Message"]] = relationship(back_populates="call")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    role: Mapped[str] = mapped_column(String(12))  # caller|assistant
    text: Mapped[str] = mapped_column(Text)
    intent: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=local_now)
    call: Mapped[CallSession] = relationship(back_populates="messages")


class ToolCallLog(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    call_id: Mapped[int | None] = mapped_column(ForeignKey("call_sessions.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=local_now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    # ponytail: role as a string column; separate roles/permissions tables when
    # anything finer than admin|staff exists.
    role: Mapped[str] = mapped_column(String(16), default="staff")


class PMSRecord(Base):
    """Mock PMS/EHR store: what the write-back API persists. Idempotency-keyed."""
    __tablename__ = "pms_records"
    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(24))  # appointment|cancellation
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=local_now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(120))  # user email or "voice-agent"
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(), default=local_now)
