"""End-to-end conversation tests through the state machine with a scripted LLM."""
from datetime import date, datetime, timedelta

from sqlalchemy import select

from packages.conversation.engine import ConversationEngine
from packages.database.models import Appointment, CallSession, Patient
from tests.conftest import ScriptedLLM


def next_weekday() -> str:
    day = date.today() + timedelta(days=1)
    while day.weekday() > 4:
        day += timedelta(days=1)
    return day.isoformat()


async def new_call(db, sid="CA1"):
    call = CallSession(sid=sid, caller="+919876543210")
    db.add(call)
    await db.flush()
    return call


async def test_full_booking_flow(db):
    day = next_weekday()
    llm = ScriptedLLM([
        {"intent": "book", "confidence": 0.95, "language": "en"},   # classify
        {"name": "Ravi Kumar", "phone": "9876543210", "dob": "1990-03-05"},  # extract
        {"doctor": "Asha", "date": day, "time": "10:00"},           # extract rest
    ])
    engine = ConversationEngine(llm)
    call = await new_call(db)

    reply = await engine.handle(db, call, None)  # greeting
    assert "how can i help" in reply.say.lower()

    reply = await engine.handle(db, call, "I want to book an appointment, I'm Ravi Kumar, "
                                          "phone 9876543210, born March 5th 1990")
    assert call.state == "collect"
    assert "doctor" in reply.say.lower() or "department" in reply.say.lower()

    reply = await engine.handle(db, call, "Dr Asha, tomorrow at 10 am")
    assert call.state == "confirm"
    assert "Ravi Kumar" in reply.say

    reply = await engine.handle(db, call, "yes please")
    assert "confirmation number" in reply.say.lower()
    appt = (await db.execute(select(Appointment))).scalars().first()
    assert appt is not None and appt.status == "booked"
    assert appt.starts_at == datetime.fromisoformat(f"{day}T10:00:00")
    assert call.outcome == "booked"


async def test_double_booking_offers_alternatives(db):
    day = next_weekday()
    # existing appointment at 10:00
    patient = (await db.execute(select(Patient))).scalars().first()
    db.add(Appointment(patient_id=patient.id, doctor_id=1, branch_id=1,
                       starts_at=datetime.fromisoformat(f"{day}T10:00:00")))
    await db.commit()

    llm = ScriptedLLM([
        {"intent": "book", "confidence": 0.9, "language": "en"},
        {"name": "Anita Shah", "phone": "9123456789", "dob": "1985-06-01",
         "doctor": "Asha", "date": day, "time": "10:00"},
    ])
    engine = ConversationEngine(llm)
    call = await new_call(db, "CA2")
    await engine.handle(db, call, None)
    reply = await engine.handle(db, call, "book me with Dr Asha at 10")
    assert "taken" in reply.say.lower() and call.state == "collect"


async def test_cancel_flow(db):
    day = next_weekday()
    patient = (await db.execute(select(Patient))).scalars().first()
    appt = Appointment(patient_id=patient.id, doctor_id=1, branch_id=1,
                       starts_at=datetime.fromisoformat(f"{day}T11:00:00"))
    db.add(appt)
    await db.commit()

    llm = ScriptedLLM([
        {"intent": "cancel", "confidence": 0.9, "language": "en"},
        {"phone": "9876543210", "dob": "1990-03-05"},
    ])
    engine = ConversationEngine(llm)
    call = await new_call(db, "CA3")
    await engine.handle(db, call, None)
    reply = await engine.handle(db, call, "cancel my appointment, 9876543210, March 5 1990")
    assert call.state == "confirm" and "cancel" in reply.say.lower()

    reply = await engine.handle(db, call, "yes")
    assert "cancelled" in reply.say.lower()
    await db.refresh(appt)
    assert appt.status == "cancelled"


async def test_emergency_is_deterministic(db):
    engine = ConversationEngine(ScriptedLLM([]))  # LLM never consulted
    call = await new_call(db, "CA4")
    await engine.handle(db, call, None)
    reply = await engine.handle(db, call, "I have chest pain right now")
    assert reply.action == "hangup" and "emergency" in reply.say.lower()


async def test_invalid_input_retries_then_transfers(db):
    responses = [{"intent": "unknown", "confidence": 0.1, "language": "en"}] * 5
    engine = ConversationEngine(ScriptedLLM(responses))
    call = await new_call(db, "CA5")
    await engine.handle(db, call, None)
    replies = [await engine.handle(db, call, "blub glorp") for _ in range(3)]
    assert replies[-1].action == "transfer"


async def test_llm_failure_degrades_gracefully(db):
    from packages.llm.provider import LLMError

    class ExplodingLLM(ScriptedLLM):
        async def complete(self, messages, json_mode=False):
            raise LLMError("provider down")

    engine = ConversationEngine(ExplodingLLM([]))
    call = await new_call(db, "CA6")
    await engine.handle(db, call, None)
    reply = await engine.handle(db, call, "book an appointment")
    assert reply.action == "gather"  # clarify, not crash
