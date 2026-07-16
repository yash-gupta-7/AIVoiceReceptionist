"""PMS/EHR write-back after confirmed bookings.

Behavior contract (documented for the assignment):
- The local DB is the source of truth; the PMS write-back is attempted inline
  with a short timeout and NEVER blocks or fails a booking.
- Idempotent: the mock PMS enforces an Idempotency-Key; the Cliniko path is
  guarded by the stored cliniko_id (a replay is a no-op).
- On failure the appointment is marked pms_status="failed" and an audit row is
  written; retry_failed() re-drives failures.

With CLINIKO_API_KEY set, bookings create a real patient + appointment in
Cliniko (visible in its calendar) and cancellations cancel it there. A Cliniko
trial has a single practitioner/business, so every booking lands on that diary
with our doctor/branch recorded in the appointment notes.
"""
import logging
from datetime import timedelta, timezone

import httpx

from packages.database.models import Appointment, AuditLog, Patient
from packages.shared import clock
from packages.shared.config import get_settings
from packages.shared.logging import log

logger = logging.getLogger("pms")
TIMEOUT_S = 6.0
_cliniko_ids: dict = {}  # discovered once: business/practitioner/appointment_type


async def pms_sync(db, kind: str, idempotency_key: str, payload: dict) -> str:
    """Write one record to the PMS. Returns 'synced' or 'failed'."""
    s = get_settings()
    try:
        if s.cliniko_api_key:
            await _cliniko_write(db, kind, payload)
        else:
            await _mock_write(db, kind, idempotency_key, payload)
        log(logger, "pms_synced", key=idempotency_key, kind=kind)
        return "synced"
    except Exception as exc:
        logger.warning("pms write-back failed for %s: %s", idempotency_key, exc)
        db.add(AuditLog(actor="pms", action="pms_sync_failed",
                        detail={"key": idempotency_key, "kind": kind,
                                "payload": payload, "error": str(exc)}))
        return "failed"


async def _mock_write(db, kind: str, idempotency_key: str, payload: dict) -> None:
    """Built-in mock PMS: HTTP call to our own /api/mock-pms endpoint so the
    write-back exercises a real network hop, timeout and idempotency header."""
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        resp = await client.post(
            f"{get_settings().public_url}/api/mock-pms/records",
            headers={"Idempotency-Key": idempotency_key},
            json={"kind": kind, "payload": payload},
        )
        resp.raise_for_status()


def _cliniko_client() -> httpx.AsyncClient:
    s = get_settings()
    return httpx.AsyncClient(
        timeout=TIMEOUT_S, base_url=s.cliniko_base_url,
        auth=(s.cliniko_api_key, ""),
        headers={"Accept": "application/json",
                 "User-Agent": "city-dental-receptionist (kuriachanabe05@gmail.com)"},
    )


def _utc(dt) -> str:
    return dt.replace(tzinfo=clock.tz()).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _cliniko_ids_cached(client: httpx.AsyncClient) -> dict:
    if not _cliniko_ids:
        business = (await client.get("/businesses")).json()["businesses"][0]
        practitioner = (await client.get("/practitioners")).json()["practitioners"][0]
        _cliniko_ids.update(
            business_id=business["id"], practitioner_id=practitioner["id"],
            appointment_type_id=business["appointment_type_ids"][0],
        )
    return _cliniko_ids


async def _cliniko_write(db, kind: str, payload: dict) -> None:
    appt = await db.get(Appointment, payload["appointment_id"])
    if appt is None:
        raise ValueError("appointment not found for PMS sync")

    async with _cliniko_client() as client:
        if kind == "cancellation":
            if appt.cliniko_id:
                resp = await client.patch(
                    f"/individual_appointments/{appt.cliniko_id}/cancel",
                    json={"cancellation_reason": 50},  # 50 = other
                )
                resp.raise_for_status()
            return

        ids = await _cliniko_ids_cached(client)
        patient = await db.get(Patient, appt.patient_id)
        if patient.cliniko_id is None:
            first, _, last = patient.name.partition(" ")
            resp = await client.post("/patients", json={
                "first_name": first, "last_name": last or "-",
                "date_of_birth": patient.dob.isoformat(),
                "patient_phone_numbers": [{"number": patient.phone,
                                           "phone_type": "Mobile"}],
            })
            resp.raise_for_status()
            patient.cliniko_id = resp.json()["id"]

        starts, ends = appt.starts_at, appt.starts_at + timedelta(minutes=30)
        if appt.cliniko_id:  # reschedule: move the existing PMS appointment
            resp = await client.patch(f"/individual_appointments/{appt.cliniko_id}",
                                      json={"starts_at": _utc(starts), "ends_at": _utc(ends)})
            resp.raise_for_status()
            return
        doctor_note = payload.get("doctor", ""), payload.get("branch", "")
        resp = await client.post("/individual_appointments", json={
            "patient_id": patient.cliniko_id,
            "practitioner_id": ids["practitioner_id"],
            "business_id": ids["business_id"],
            "appointment_type_id": ids["appointment_type_id"],
            "starts_at": _utc(starts), "ends_at": _utc(ends),
            "notes": f"Voice agent booking #{appt.id} — {doctor_note[0]} @ {doctor_note[1]}",
        })
        resp.raise_for_status()
        appt.cliniko_id = resp.json()["id"]


async def retry_failed(db) -> int:
    """Re-drive failed PMS writes. Returns how many synced."""
    from sqlalchemy import select

    failed = (
        await db.execute(select(Appointment).where(Appointment.pms_status == "failed"))
    ).scalars().all()
    count = 0
    for appt in failed:
        status = await pms_sync(db, "appointment", f"appt-{appt.id}",
                                {"appointment_id": appt.id,
                                 "starts_at": appt.starts_at.isoformat()})
        appt.pms_status = status
        count += status == "synced"
    await db.commit()
    return count
