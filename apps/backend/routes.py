"""Dashboard REST API. Routes stay thin: validate, call business logic, serialize."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.backend import schemas
from apps.backend.auth import create_token, current_user, verify_password
from packages.conversation import tools
from packages.database.models import (
    Appointment, AuditLog, CallSession, Doctor, Message, Patient, User,
)
from packages.database.session import get_db
from packages.shared import clock
from packages.scheduler.availability import free_slots

router = APIRouter(prefix="/api")


@router.post("/auth/login", response_model=schemas.TokenOut, tags=["auth"])
async def login(body: schemas.LoginIn, db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.email == body.email))).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    return schemas.TokenOut(access_token=create_token(user.email, user.role))


@router.get("/patients", response_model=list[schemas.PatientOut], tags=["patients"])
async def list_patients(q: str = "", db: AsyncSession = Depends(get_db),
                        _: dict = Depends(current_user)):
    stmt = select(Patient).order_by(Patient.id.desc()).limit(200)
    if q:
        stmt = stmt.where(Patient.name.ilike(f"%{q}%") | Patient.phone.contains(q))
    return (await db.execute(stmt)).scalars().all()


@router.post("/patients", response_model=schemas.PatientOut, status_code=201, tags=["patients"])
async def create_patient(body: schemas.PatientIn, db: AsyncSession = Depends(get_db),
                         user: dict = Depends(current_user)):
    patient = Patient(**body.model_dump())
    db.add(patient)
    db.add(AuditLog(actor=user["sub"], action="patient_created", detail={"phone": body.phone}))
    try:
        await db.commit()
    except IntegrityError:
        raise HTTPException(409, "A patient with that phone number already exists")
    return patient


@router.get("/doctors", response_model=list[schemas.DoctorOut], tags=["doctors"])
async def list_doctors(db: AsyncSession = Depends(get_db), _: dict = Depends(current_user)):
    return (await db.execute(select(Doctor))).scalars().all()


@router.get("/doctors/{doctor_id}/availability", tags=["doctors"])
async def doctor_availability(doctor_id: int, day: date,
                              db: AsyncSession = Depends(get_db),
                              _: dict = Depends(current_user)):
    return {"slots": [s.isoformat() for s in await free_slots(db, doctor_id, day)]}


@router.get("/appointments", response_model=list[schemas.AppointmentOut], tags=["appointments"])
async def list_appointments(status: str = "", db: AsyncSession = Depends(get_db),
                            _: dict = Depends(current_user)):
    stmt = select(Appointment).order_by(Appointment.starts_at.desc()).limit(200)
    if status:
        stmt = stmt.where(Appointment.status == status)
    return (await db.execute(stmt)).scalars().all()


@router.post("/appointments", response_model=schemas.AppointmentOut, status_code=201,
             tags=["appointments"])
async def create_appointment(body: schemas.AppointmentIn, db: AsyncSession = Depends(get_db),
                             user: dict = Depends(current_user)):
    doctor = await db.get(Doctor, body.doctor_id)
    if doctor is None:
        raise HTTPException(404, "Doctor not found")
    starts = clock.to_local_naive(body.starts_at)
    if starts not in await free_slots(db, doctor.id, starts.date()):
        raise HTTPException(409, "Slot not available")
    try:
        appt = await tools.book_appointment(db, tools.BookIn(
            patient_id=body.patient_id, doctor_id=doctor.id,
            branch_id=doctor.branch_id, starts_at=starts))
    except tools.ToolError:
        raise HTTPException(409, "Slot not available")
    db.add(AuditLog(actor=user["sub"], action="booked", detail={"appointment_id": appt.id}))
    await db.commit()
    return appt


@router.delete("/appointments/{appointment_id}", response_model=schemas.AppointmentOut,
               tags=["appointments"])
async def cancel_appointment(appointment_id: int, db: AsyncSession = Depends(get_db),
                             user: dict = Depends(current_user)):
    try:
        appt = await tools.cancel_appointment(db, tools.CancelIn(appointment_id=appointment_id))
    except tools.ToolError:
        raise HTTPException(404, "Appointment not found or already cancelled")
    db.add(AuditLog(actor=user["sub"], action="cancelled",
                    detail={"appointment_id": appointment_id}))
    await db.commit()
    return appt


@router.get("/calls", response_model=list[schemas.CallOut], tags=["calls"])
async def list_calls(db: AsyncSession = Depends(get_db), _: dict = Depends(current_user)):
    stmt = select(CallSession).order_by(CallSession.started_at.desc()).limit(100)
    return (await db.execute(stmt)).scalars().all()


@router.get("/calls/{call_id}/messages", response_model=list[schemas.MessageOut], tags=["calls"])
async def call_messages(call_id: int, db: AsyncSession = Depends(get_db),
                        _: dict = Depends(current_user)):
    stmt = select(Message).where(Message.call_id == call_id).order_by(Message.id)
    return (await db.execute(stmt)).scalars().all()


@router.get("/audit", tags=["admin"])
async def audit_logs(db: AsyncSession = Depends(get_db), _: dict = Depends(current_user)):
    rows = (await db.execute(
        select(AuditLog).order_by(AuditLog.id.desc()).limit(200))).scalars().all()
    return [{"id": r.id, "actor": r.actor, "action": r.action,
             "detail": r.detail, "created_at": r.created_at} for r in rows]


@router.get("/metrics", tags=["admin"])
async def metrics(db: AsyncSession = Depends(get_db), _: dict = Depends(current_user)):
    async def count(stmt):
        return (await db.execute(stmt)).scalar() or 0
    today = clock.today()
    return {
        "calls_total": await count(select(func.count(CallSession.id))),
        "calls_active": await count(select(func.count(CallSession.id))
                                    .where(CallSession.ended_at.is_(None))),
        "appointments_booked": await count(select(func.count(Appointment.id))
                                           .where(Appointment.status == "booked")),
        "patients_total": await count(select(func.count(Patient.id))),
        "appointments_today": await count(
            select(func.count(Appointment.id)).where(
                func.date(Appointment.starts_at) == today,
                Appointment.status == "booked")),
    }
