"""Slot availability. All datetimes are naive clinic-local (see packages/shared/clock)."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.database.models import Appointment, Branch, Doctor, Schedule
from packages.shared import clock


async def free_slots(db: AsyncSession, doctor_id: int, day: date) -> list[datetime]:
    """Open appointment start times for a doctor on a day, honouring the
    schedule's slot length and required buffer between appointments."""
    schedule = (
        await db.execute(
            select(Schedule).where(
                Schedule.doctor_id == doctor_id, Schedule.weekday == day.weekday()
            )
        )
    ).scalar_one_or_none()
    if schedule is None:
        return []

    taken = set(
        (
            await db.execute(
                select(Appointment.starts_at).where(
                    Appointment.doctor_id == doctor_id,
                    Appointment.status == "booked",
                    Appointment.starts_at >= datetime.combine(day, time.min),
                    Appointment.starts_at < datetime.combine(day, time.max),
                )
            )
        ).scalars()
    )

    slots: list[datetime] = []
    cursor = datetime.combine(day, schedule.start)
    end = datetime.combine(day, schedule.end)
    step = timedelta(minutes=schedule.slot_minutes + schedule.buffer_minutes)
    appt_len = timedelta(minutes=schedule.slot_minutes)
    now = clock.now()
    while cursor + appt_len <= end:
        if cursor not in taken and cursor > now:
            slots.append(cursor)
        cursor += step
    return slots


@dataclass
class SlotOption:
    doctor_id: int
    doctor: str
    department: str
    branch_id: int
    branch: str
    starts_at: datetime


async def search_slots(
    db: AsyncSession,
    *,
    doctor_id: int | None = None,
    department: str | None = None,
    branch_id: int | None = None,
    on_date: date | None = None,
    weekdays: list[int] | None = None,
    after: time | None = None,
    before: time | None = None,
    days_ahead: int = 14,
    limit: int = 8,
) -> list[SlotOption]:
    """Resolve fuzzy availability queries against live data, across ALL
    practitioners and branches unless narrowed. Supports:
    "Dec 13 around 1" -> on_date + after/before window
    "Mondays and Wednesdays" -> weekdays=[0, 2]
    "any Thursday morning" -> weekdays=[3], before=12:00
    "earliest slot anywhere" -> no filters, limit=1
    """
    stmt = select(Doctor)
    if doctor_id:
        stmt = stmt.where(Doctor.id == doctor_id)
    if department:
        stmt = stmt.where(Doctor.department.ilike(f"%{department}%"))
    if branch_id:
        stmt = stmt.where(Doctor.branch_id == branch_id)
    doctors = list((await db.execute(stmt)).scalars())
    if not doctors:
        return []
    branches = {d.branch_id: await db.get(Branch, d.branch_id) for d in doctors}

    days = (
        [on_date]
        if on_date
        else [clock.today() + timedelta(days=i) for i in range(days_ahead)]
    )
    results: list[SlotOption] = []
    for day in days:
        if weekdays is not None and day.weekday() not in weekdays:
            continue
        for doctor in doctors:
            for slot in await free_slots(db, doctor.id, day):
                if after and slot.time() < after:
                    continue
                if before and slot.time() >= before:
                    continue
                branch = branches[doctor.branch_id]
                results.append(SlotOption(
                    doctor_id=doctor.id, doctor=doctor.name, department=doctor.department,
                    branch_id=branch.id, branch=branch.name, starts_at=slot,
                ))
    results.sort(key=lambda s: s.starts_at)
    return results[:limit]


async def is_free(db: AsyncSession, doctor_id: int, starts_at: datetime) -> bool:
    return starts_at in await free_slots(db, doctor_id, starts_at.date())
