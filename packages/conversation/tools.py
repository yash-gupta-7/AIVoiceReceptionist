"""Typed tools the conversation engine can execute against business logic.

Each tool: pydantic input schema, timeout, error capture, and an audit row in
tool_calls. All DB writes for a call go through here — never from routes or
prompts.
"""
import asyncio
import logging
import time as time_mod
from datetime import date, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import Appointment, Branch, Doctor, Patient, ToolCallLog
from packages.scheduler.availability import free_slots
from packages.shared import clock
from packages.shared.logging import log

logger = logging.getLogger("tools")
TOOL_TIMEOUT_S = 5.0


class ToolError(Exception):
    """Tool failed; the engine converts this into a graceful spoken recovery."""


class FindPatientIn(BaseModel):
    phone: str
    dob: date | None = None


class CreatePatientIn(BaseModel):
    name: str
    phone: str
    dob: date


class BookIn(BaseModel):
    patient_id: int
    doctor_id: int
    branch_id: int
    starts_at: datetime


class CancelIn(BaseModel):
    appointment_id: int


class RescheduleIn(BaseModel):
    appointment_id: int
    starts_at: datetime


async def find_patients_by_phone(db: AsyncSession, phone: str) -> list[Patient]:
    """A phone number can be a family line shared by several patients."""
    return list((await db.execute(select(Patient).where(Patient.phone == phone))).scalars())


async def find_patient(db: AsyncSession, args: FindPatientIn) -> Patient | None:
    # ponytail: phone(+dob) is the caller-identity check; OTP later if spoofing matters
    for patient in await find_patients_by_phone(db, args.phone):
        if args.dob is None or patient.dob == args.dob:
            return patient
    return None


async def create_patient(db: AsyncSession, args: CreatePatientIn) -> Patient:
    patient = Patient(name=args.name, phone=args.phone, dob=args.dob)
    db.add(patient)
    await db.flush()
    return patient


async def lookup_doctor(db: AsyncSession, query: str) -> Doctor | None:
    """Case-insensitive match on doctor name or department."""
    query = query.lower().strip().removeprefix("dr.").removeprefix("dr ").strip()
    for doctor in (await db.execute(select(Doctor))).scalars():
        if query in doctor.name.lower() or query in doctor.department.lower():
            return doctor
    return None


async def lookup_schedule(db: AsyncSession, doctor_id: int, day: date) -> list[datetime]:
    return await free_slots(db, doctor_id, day)


async def lookup_branch(db: AsyncSession) -> Branch | None:
    return (await db.execute(select(Branch).limit(1))).scalar_one_or_none()


async def find_upcoming_appointment(db: AsyncSession, patient_id: int) -> Appointment | None:
    return (
        await db.execute(
            select(Appointment)
            .where(
                Appointment.patient_id == patient_id,
                Appointment.status == "booked",
                Appointment.starts_at >= clock.now(),
            )
            .order_by(Appointment.starts_at)
            .limit(1)
        )
    ).scalar_one_or_none()


async def book_appointment(db: AsyncSession, args: BookIn) -> Appointment:
    appt = Appointment(
        patient_id=args.patient_id,
        doctor_id=args.doctor_id,
        branch_id=args.branch_id,
        starts_at=args.starts_at,
    )
    db.add(appt)
    try:
        await db.flush()
    except IntegrityError as exc:  # uq_doctor_slot: someone booked it first
        await db.rollback()
        raise ToolError("slot_taken") from exc
    return appt


async def cancel_appointment(db: AsyncSession, args: CancelIn) -> Appointment:
    appt = await db.get(Appointment, args.appointment_id)
    if appt is None or appt.status != "booked":
        raise ToolError("not_found")
    appt.status = "cancelled"
    await db.flush()
    return appt


async def reschedule_appointment(db: AsyncSession, args: RescheduleIn) -> Appointment:
    appt = await db.get(Appointment, args.appointment_id)
    if appt is None or appt.status != "booked":
        raise ToolError("not_found")
    appt.starts_at = args.starts_at
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ToolError("slot_taken") from exc
    return appt


async def run_tool(db: AsyncSession, call_id: int | None, name: str, coro, tool_input: dict):
    """Execute a tool with timeout + audit logging. Raises ToolError on failure."""
    started = time_mod.monotonic()
    error: str | None = None
    try:
        return await asyncio.wait_for(coro, timeout=TOOL_TIMEOUT_S)
    except ToolError as exc:
        error = str(exc)
        raise
    except (TimeoutError, Exception) as exc:
        error = f"{type(exc).__name__}: {exc}"
        logger.exception("tool %s failed", name)
        raise ToolError("internal") from exc
    finally:
        duration = int((time_mod.monotonic() - started) * 1000)
        log(logger, "tool_call", tool=name, duration_ms=duration, error=error)
        db.add(ToolCallLog(call_id=call_id, name=name, input=tool_input,
                           error=error, duration_ms=duration))
