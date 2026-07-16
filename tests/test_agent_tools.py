"""Agent tool tests: cross-branch search, caller context, idempotent booking,
buffers, and the Vapi webhook + mock PMS endpoints."""
from datetime import date, datetime, time, timedelta

import httpx
import pytest
from sqlalchemy import select

from packages.conversation import agent_tools
from packages.database.models import (
    Appointment, Branch, CallSession, Doctor, Patient, PMSRecord, Schedule,
)
from packages.shared import clock


@pytest.fixture
async def clinic(db):
    """Add a second branch + doctor with buffered slots on top of the base fixture."""
    branch2 = Branch(name="Canary Wharf", address="15 Cabot Square", phone="+44",
                     info={"pricing": "Check-up £65"})
    db.add(branch2)
    await db.flush()
    doc2 = Doctor(name="DR. ANIL GUPTA", department="General Medicine", branch_id=branch2.id)
    db.add(doc2)
    await db.flush()
    for wd in range(7):
        db.add(Schedule(doctor_id=doc2.id, weekday=wd, start=time(10, 0), end=time(16, 0),
                        slot_minutes=30, buffer_minutes=10))
    await db.commit()
    return {"branch2": branch2, "doc2": doc2}


def tomorrow() -> date:
    return clock.today() + timedelta(days=1)


async def test_buffer_respected(db, clinic):
    result = await agent_tools.check_availability(
        db, doctor_name="Gupta", on_date=tomorrow().isoformat(), limit=3)
    assert result["ok"]
    starts = [datetime.fromisoformat(s["starts_at"]) for s in result["slots"]]
    assert (starts[1] - starts[0]) == timedelta(minutes=40)  # 30 slot + 10 buffer


async def test_earliest_slot_crosses_branches(db, clinic):
    # fill doctor 1 (branch 1) for the whole search horizon, so the earliest
    # opening anywhere is Dr Gupta at branch 2 — regardless of time of day
    patient = (await db.execute(select(Patient))).scalars().first()
    from packages.scheduler.availability import free_slots
    for offset in range(15):
        day = clock.today() + timedelta(days=offset)
        for slot in await free_slots(db, 1, day):
            db.add(Appointment(patient_id=patient.id, doctor_id=1, branch_id=1,
                               starts_at=slot))
    await db.commit()

    result = await agent_tools.find_earliest_slot(db)
    assert result["ok"] and result["slots"]
    assert result["slots"][0]["branch"] == "Canary Wharf"


async def test_weekday_and_window_filters(db, clinic):
    result = await agent_tools.check_availability(
        db, weekdays=["thursday"], before="12:00", limit=6)
    assert result["ok"]
    for s in result["slots"]:
        dt = datetime.fromisoformat(s["starts_at"])
        assert dt.weekday() == 3 and dt.time() < time(12, 0)


async def test_caller_context_family_line_and_dropped_call(db, clinic):
    db.add(Patient(name="Maria Lopez", phone="4477001005", dob=date(1970, 2, 2)))
    db.add(Patient(name="Sofia Lopez", phone="4477001005", dob=date(2005, 8, 14)))
    db.add(CallSession(sid="dropped", caller="+4477001005", outcome=None,
                       started_at=clock.now() - timedelta(minutes=5),
                       data={"summary": "booking in progress"}))
    await db.commit()

    ctx = await agent_tools.get_caller_context(db, "+4477001005")
    assert len(ctx["known_patients"]) == 2  # family line -> agent must disambiguate
    assert ctx["dropped_call"]["summary"] == "booking in progress"
    assert ctx["today"] == clock.today().isoformat()


async def test_booking_idempotent_and_requires_name(db, clinic):
    slot = (await agent_tools.check_availability(
        db, doctor_name="Gupta", on_date=tomorrow().isoformat()))["slots"][0]

    missing_name = await agent_tools.book_appointment(
        db, patient_name="", phone="4470001", doctor_id=slot["doctor_id"],
        starts_at=slot["starts_at"])
    assert not missing_name["ok"]  # anonymous bookings never go through

    first = await agent_tools.book_appointment(
        db, patient_name="Chloe Davis", phone="4470001", doctor_id=slot["doctor_id"],
        starts_at=slot["starts_at"], dob="1995-09-09")
    assert first["ok"] and first["branch"] == "Canary Wharf"
    replay = await agent_tools.book_appointment(
        db, patient_name="Chloe Davis", phone="4470001", doctor_id=slot["doctor_id"],
        starts_at=slot["starts_at"])
    assert replay["ok"] and replay["appointment_id"] == first["appointment_id"]

    # another caller hitting the same slot gets alternatives, not a silent failure
    rival = await agent_tools.book_appointment(
        db, patient_name="Someone Else", phone="4470002", doctor_id=slot["doctor_id"],
        starts_at=slot["starts_at"])
    assert not rival["ok"] and rival["error"] == "slot_no_longer_available"
    assert rival["alternatives"]


async def test_vapi_webhook_dispatch_and_pms_idempotency(db, clinic):
    from apps.backend.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # tool-calls dispatch
        resp = await client.post("/api/vapi/webhook", json={
            "message": {
                "type": "tool-calls",
                "call": {"id": "vapi-call-1", "customer": {"number": "+4477009999"}},
                "toolCallList": [{
                    "id": "tc1",
                    "function": {"name": "get_caller_context", "arguments": "{}"},
                }],
            }})
        assert resp.status_code == 200
        assert "known_patients" in resp.json()["results"][0]["result"]

        # mock PMS: idempotent on Idempotency-Key
        r1 = await client.post("/api/mock-pms/records", json={"kind": "appointment",
                               "payload": {"x": 1}}, headers={"Idempotency-Key": "k1"})
        r2 = await client.post("/api/mock-pms/records", json={"kind": "appointment",
                               "payload": {"x": 1}}, headers={"Idempotency-Key": "k1"})
        assert r1.json()["replayed"] is False and r2.json()["replayed"] is True
        records = (await db.execute(
            select(PMSRecord).where(PMSRecord.idempotency_key == "k1"))).scalars().all()
        assert len(records) == 1

        # simulated outage
        fail = await client.post("/api/mock-pms/records?fail=1", json={},
                                 headers={"Idempotency-Key": "k2"})
        assert fail.status_code == 503


async def test_follow_up_logged(db, clinic):
    result = await agent_tools.log_follow_up(db, "+4477", "billing complaint")
    assert result["ok"]
