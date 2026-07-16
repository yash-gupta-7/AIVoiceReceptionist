"""Platform-agnostic agent tools: the contract between the voice platform
(Vapi) and the backend. The eval harness drives these same functions, so what
we test offline is exactly what the live agent calls.

Every tool returns a JSON-serializable dict with "ok" plus data or "error".
"""
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.conversation import tools
from packages.database.models import (
    Appointment, AuditLog, Branch, CallSession, Doctor, Patient,
)
from packages.pms.writeback import pms_sync
from packages.scheduler.availability import free_slots, search_slots
from packages.shared import clock

logger = logging.getLogger("agent_tools")

WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _spoken(dt: datetime) -> str:
    return dt.strftime("%A %d %B at %I:%M %p").replace(" 0", " ")


def _slot_dict(s) -> dict:
    return {
        "doctor_id": s.doctor_id, "doctor": s.doctor, "department": s.department,
        "branch_id": s.branch_id, "branch": s.branch,
        "starts_at": s.starts_at.isoformat(), "spoken": _spoken(s.starts_at),
    }


async def _resolve_branch(db: AsyncSession, branch_name: str | None) -> Branch | None:
    if not branch_name:
        return None
    return (
        await db.execute(select(Branch).where(Branch.name.ilike(f"%{branch_name}%")))
    ).scalars().first()


async def get_caller_context(db: AsyncSession, phone: str) -> dict:
    """Recognize returning patients, family lines, dropped calls, and pending
    callbacks — called once at the start of every call."""
    phone = "".join(c for c in phone if c.isdigit())[-10:]
    patients = [
        {"patient_id": p.id, "name": p.name, "dob": p.dob.isoformat()}
        for p in (
            await db.execute(select(Patient).where(Patient.phone.contains(phone))))
        .scalars()
    ]
    upcoming = []
    for p in patients:
        for a in (
            await db.execute(
                select(Appointment).where(
                    Appointment.patient_id == p["patient_id"],
                    Appointment.status == "booked",
                    Appointment.starts_at >= clock.now(),
                ).order_by(Appointment.starts_at))
        ).scalars():
            doctor = await db.get(Doctor, a.doctor_id)
            branch = await db.get(Branch, a.branch_id)
            upcoming.append({
                "appointment_id": a.id, "patient": p["name"], "doctor": doctor.name,
                "branch": branch.name, "starts_at": a.starts_at.isoformat(),
                "spoken": _spoken(a.starts_at),
            })

    # dropped / incomplete call in the last 2h -> resume, don't restart
    recent = (
        await db.execute(
            select(CallSession)
            .where(
                CallSession.caller.contains(phone),
                CallSession.outcome.is_(None),
                CallSession.started_at >= clock.now() - timedelta(hours=2),
            )
            .order_by(CallSession.started_at.desc()).limit(1))
    ).scalars().first()
    dropped = None
    if recent:
        dropped = {"summary": recent.data.get("summary", ""), "at": recent.started_at.isoformat()}

    # missed outbound call from the clinic in the last 48h -> this is a callback
    outbound = (
        await db.execute(
            select(AuditLog)
            .where(
                AuditLog.action.in_(["outbound_missed", "follow_up"]),
                AuditLog.created_at >= clock.now() - timedelta(hours=48),
            )
            .order_by(AuditLog.id.desc()))
    ).scalars()
    callback_reason = next(
        (o.detail.get("reason") for o in outbound if phone in str(o.detail.get("phone", ""))),
        None,
    )

    return {
        "ok": True,
        "known_patients": patients,  # >1 => family line: ask WHO is calling first
        "upcoming_appointments": upcoming,
        "dropped_call": dropped,
        "callback_context": callback_reason,
        "today": clock.today().isoformat(),
        "now": clock.now().strftime("%A %d %B, %I:%M %p"),
    }


async def list_clinic_info(db: AsyncSession) -> dict:
    branches = [
        {"branch_id": b.id, "name": b.name, "address": b.address, "info": b.info}
        for b in (await db.execute(select(Branch))).scalars()
    ]
    doctors = [
        {"doctor_id": d.id, "name": d.name, "department": d.department, "branch_id": d.branch_id}
        for d in (await db.execute(select(Doctor))).scalars()
    ]
    return {"ok": True, "branches": branches, "doctors": doctors}


async def check_availability(
    db: AsyncSession,
    doctor_name: str | None = None,
    department: str | None = None,
    branch_name: str | None = None,
    on_date: str | None = None,
    weekdays: list[str] | None = None,
    after: str | None = None,
    before: str | None = None,
    limit: int = 6,
) -> dict:
    """ALWAYS call this for availability — never answer from memory. Supports
    fuzzy asks: a specific date, weekday preferences, and time-of-day windows."""
    doctor = await tools.lookup_doctor(db, doctor_name) if doctor_name else None
    if doctor_name and doctor is None:
        return {"ok": False, "error": f"no doctor matching '{doctor_name}'"}
    branch = await _resolve_branch(db, branch_name)
    if branch_name and branch is None:
        return {"ok": False, "error": f"no branch matching '{branch_name}'"}
    try:
        day = date.fromisoformat(on_date) if on_date else None
        wd = [WEEKDAY_NAMES.index(w.lower()) for w in weekdays] if weekdays else None
        t_after = time.fromisoformat(after) if after else None
        t_before = time.fromisoformat(before) if before else None
    except ValueError as exc:
        return {"ok": False, "error": f"bad date/time format: {exc}"}
    if day and day < clock.today():
        return {"ok": False, "error": "date is in the past", "today": clock.today().isoformat()}

    slots = await search_slots(
        db, doctor_id=doctor.id if doctor else None, department=department,
        branch_id=branch.id if branch else None, on_date=day, weekdays=wd,
        after=t_after, before=t_before, limit=limit,
    )
    return {"ok": True, "slots": [_slot_dict(s) for s in slots],
            "checked_at": clock.now().isoformat()}


async def find_earliest_slot(
    db: AsyncSession,
    department: str | None = None,
    branch_name: str | None = None,
    same_day_only: bool = False,
) -> dict:
    """Earliest opening across ALL practitioners and ALL branches (unless narrowed)."""
    branch = await _resolve_branch(db, branch_name)
    if branch_name and branch is None:
        return {"ok": False, "error": f"no branch matching '{branch_name}'"}
    slots = await search_slots(
        db, department=department, branch_id=branch.id if branch else None,
        on_date=clock.today() if same_day_only else None,
        days_ahead=1 if same_day_only else 14, limit=3,
    )
    if not slots:
        return {"ok": True, "slots": [],
                "note": "nothing available in this window" + (" today" if same_day_only else "")}
    return {"ok": True, "slots": [_slot_dict(s) for s in slots]}


async def book_appointment(
    db: AsyncSession,
    patient_name: str,
    phone: str,
    doctor_id: int,
    starts_at: str,
    dob: str | None = None,
    call_sid: str | None = None,
) -> dict:
    """Book after explicit caller confirmation. Live-rechecks the slot,
    creates the patient if new, and write-backs to the PMS (idempotent)."""
    if not patient_name or len(patient_name.strip()) < 2:
        return {"ok": False, "error": "full patient name is required before booking"}
    doctor = await db.get(Doctor, doctor_id)
    if doctor is None:
        return {"ok": False, "error": "unknown doctor_id"}
    try:
        when = clock.to_local_naive(datetime.fromisoformat(starts_at))
    except ValueError:
        return {"ok": False, "error": "starts_at must be ISO datetime"}

    phone = "".join(c for c in phone if c.isdigit())
    patient = None
    wanted = patient_name.lower().strip()
    for p in await tools.find_patients_by_phone(db, phone):
        known = p.name.lower()
        # tolerate partial names ("David" vs "David Evans") — same phone + name
        # overlap is the same person, not a new record
        if known == wanted or wanted in known or known in wanted:
            patient = p
            break
    if patient is None:
        patient = await tools.create_patient(db, tools.CreatePatientIn(
            name=patient_name.strip(), phone=phone,
            dob=date.fromisoformat(dob) if dob else date(1900, 1, 1)))

    # idempotency: same patient+doctor+time already booked -> return it, don't duplicate
    existing = (
        await db.execute(select(Appointment).where(
            Appointment.patient_id == patient.id, Appointment.doctor_id == doctor.id,
            Appointment.starts_at == when, Appointment.status == "booked"))
    ).scalars().first()
    if existing:
        appt = existing
    else:
        if when not in await free_slots(db, doctor.id, when.date()):
            alternatives = await search_slots(db, doctor_id=doctor.id,
                                              on_date=when.date(), limit=3)
            return {"ok": False, "error": "slot_no_longer_available",
                    "alternatives": [_slot_dict(s) for s in alternatives]}
        try:
            appt = await tools.book_appointment(db, tools.BookIn(
                patient_id=patient.id, doctor_id=doctor.id,
                branch_id=doctor.branch_id, starts_at=when))
        except tools.ToolError:
            return {"ok": False, "error": "slot_no_longer_available"}

    branch = await db.get(Branch, appt.branch_id)
    appt.pms_status = await pms_sync(db, "appointment", f"appt-{appt.id}", {
        "appointment_id": appt.id, "patient": patient.name, "phone": patient.phone,
        "doctor": doctor.name, "branch": branch.name, "starts_at": when.isoformat(),
    })
    db.add(AuditLog(actor="voice-agent", action="booked",
                    detail={"appointment_id": appt.id, "call_sid": call_sid or ""}))
    await db.commit()
    return {
        "ok": True, "appointment_id": appt.id, "patient": patient.name,
        "doctor": doctor.name, "branch": branch.name,  # say THIS branch aloud
        "starts_at": when.isoformat(), "spoken": _spoken(when),
        "pms_status": appt.pms_status,
    }


async def find_appointments(db: AsyncSession, phone: str, patient_name: str | None = None) -> dict:
    ctx = await get_caller_context(db, phone)
    appts = ctx["upcoming_appointments"]
    if patient_name:
        appts = [a for a in appts if patient_name.lower() in a["patient"].lower()]
    return {"ok": True, "appointments": appts}


async def cancel_appointment(db: AsyncSession, appointment_id: int) -> dict:
    try:
        appt = await tools.cancel_appointment(db, tools.CancelIn(appointment_id=appointment_id))
    except tools.ToolError:
        return {"ok": False, "error": "appointment not found or already cancelled"}
    await pms_sync(db, "cancellation", f"cancel-{appt.id}", {"appointment_id": appt.id})
    db.add(AuditLog(actor="voice-agent", action="cancelled",
                    detail={"appointment_id": appointment_id}))
    await db.commit()
    return {"ok": True, "appointment_id": appointment_id}


async def reschedule_appointment(db: AsyncSession, appointment_id: int, new_starts_at: str) -> dict:
    appt = await db.get(Appointment, appointment_id)
    if appt is None or appt.status != "booked":
        return {"ok": False, "error": "appointment not found or not active"}
    try:
        when = clock.to_local_naive(datetime.fromisoformat(new_starts_at))
    except ValueError:
        return {"ok": False, "error": "new_starts_at must be ISO datetime"}
    if when not in await free_slots(db, appt.doctor_id, when.date()):
        alternatives = await search_slots(db, doctor_id=appt.doctor_id,
                                          on_date=when.date(), limit=3)
        return {"ok": False, "error": "slot_no_longer_available",
                "alternatives": [_slot_dict(s) for s in alternatives]}
    try:
        await tools.reschedule_appointment(db, tools.RescheduleIn(
            appointment_id=appointment_id, starts_at=when))
    except tools.ToolError:
        return {"ok": False, "error": "slot_no_longer_available"}
    await pms_sync(db, "appointment", f"resched-{appt.id}-{when.isoformat()}",
                   {"appointment_id": appt.id, "starts_at": when.isoformat()})
    db.add(AuditLog(actor="voice-agent", action="rescheduled",
                    detail={"appointment_id": appointment_id, "to": when.isoformat()}))
    await db.commit()
    doctor = await db.get(Doctor, appt.doctor_id)
    branch = await db.get(Branch, appt.branch_id)
    return {"ok": True, "appointment_id": appointment_id, "doctor": doctor.name,
            "branch": branch.name, "starts_at": when.isoformat(), "spoken": _spoken(when)}


async def log_follow_up(db: AsyncSession, phone: str, reason: str) -> dict:
    """Log anything needing human follow-up (clinical questions, complaints,
    human requests). Someone will call back — never claim a live transfer."""
    db.add(AuditLog(actor="voice-agent", action="follow_up",
                    detail={"phone": phone, "reason": reason}))
    await db.commit()
    return {"ok": True, "note": "logged; staff will call back"}


# name -> (handler, does it need the caller's number injected)
REGISTRY = {
    "get_caller_context": get_caller_context,
    "list_clinic_info": list_clinic_info,
    "check_availability": check_availability,
    "find_earliest_slot": find_earliest_slot,
    "book_appointment": book_appointment,
    "find_appointments": find_appointments,
    "cancel_appointment": cancel_appointment,
    "reschedule_appointment": reschedule_appointment,
    "log_follow_up": log_follow_up,
}
