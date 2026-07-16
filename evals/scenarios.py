"""Scripted multi-turn eval scenarios covering the assignment's required
failure modes, in English and Hindi. Each scenario: optional DB setup, a list
of caller turns (optionally with a mid-conversation hook to mutate live data),
and a check that inspects the DB + transcript afterwards."""
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from typing import Callable

from sqlalchemy import select

from packages.database.models import Appointment, AuditLog, CallSession, Patient
from packages.shared import clock


def next_weekday(target_wd: int, min_days: int = 1) -> date:
    day = clock.today() + timedelta(days=min_days)
    while day.weekday() != target_wd:
        day += timedelta(days=1)
    return day


@dataclass
class Scenario:
    name: str
    language: str  # "en" | "hi"
    phone: str
    turns: list[str]
    setup: Callable | None = None  # async (db) -> None
    hooks: dict[int, Callable] = field(default_factory=dict)  # before turn i: async (db)
    check: Callable | None = None  # async (db, transcript, tool_log) -> list[str] failures


async def _booked(db, phone: str) -> Appointment | None:
    """First booked appointment for ANY patient on this phone number."""
    ids = [p.id for p in
           (await db.execute(select(Patient).where(Patient.phone == phone))).scalars()]
    if not ids:
        return None
    return (
        await db.execute(select(Appointment).where(
            Appointment.patient_id.in_(ids), Appointment.status == "booked")
            .order_by(Appointment.id))
    ).scalars().first()


def _tool_count(tool_log, name: str) -> int:
    return sum(1 for t in tool_log if t["name"] == name)


# ---------- individual scenarios ----------

def en_fuzzy_date() -> Scenario:
    day = next_weekday(0, 3)  # a Monday, so Dr Mistry (Bank) and Dr Gupta (CW) both work
    spoken = day.strftime("%B %d")

    async def check(db, transcript, tool_log):
        failures = []
        if _tool_count(tool_log, "check_availability") < 1:
            failures.append("never called check_availability")
        appt = await _booked(db, "4477001001")
        if appt is None:
            failures.append("no appointment booked")
        elif not time(12, 0) <= appt.starts_at.time() <= time(14, 0):
            failures.append(f"booked {appt.starts_at.time()}, not 'around 1'")
        return failures

    return Scenario(
        name="en_fuzzy_date_around_1", language="en", phone="4477001001",
        turns=[
            f"Hi, do you have anything with a general dentist on {spoken} around 1?",
            "The first one works. I'm Alice Morgan, date of birth 12 May 1991.",
            "Yes, book it please.",
        ],
        check=check,
    )


def en_weekday_preference() -> Scenario:
    async def check(db, transcript, tool_log):
        failures = []
        appt = await _booked(db, "4477001002")
        if appt is None:
            failures.append("no appointment booked")
        elif appt.starts_at.weekday() not in (0, 2):
            failures.append(f"booked on weekday {appt.starts_at.weekday()}, wanted Mon/Wed")
        return failures

    return Scenario(
        name="en_weekday_preference", language="en", phone="4477001002",
        turns=[
            "I need a hygiene appointment. Mondays and Wednesdays work well for me.",
            "Hmm, none of those suit. What about orthodontics instead, same days?",
            "Take the earliest of those. My name is Ben Carter, DOB 3 March 1988.",
            "Yes confirm it.",
        ],
        check=check,
    )


def en_earliest_cross_branch() -> Scenario:
    """Fill Bank's GP diary for today so the true earliest is at Canary Wharf.
    The agent must not anchor on one practitioner/branch."""
    async def setup(db):
        from packages.scheduler.availability import free_slots
        filler = Patient(name="Filler Patient", phone="4400000000", dob=date(1980, 1, 1))
        db.add(filler)
        await db.flush()
        for slot in await free_slots(db, 1, clock.today()):  # Dr Mistry @ Bank
            db.add(Appointment(patient_id=filler.id, doctor_id=1, branch_id=1,
                               starts_at=slot))
        await db.commit()

    async def check(db, transcript, tool_log):
        failures = []
        if _tool_count(tool_log, "find_earliest_slot") < 1:
            failures.append("never called find_earliest_slot")
        appt = await _booked(db, "4477001003")
        if appt is None:
            failures.append("no appointment booked")
        elif appt.branch_id != 2:
            failures.append("anchored on Bank; earliest same-day GP slot was at Canary Wharf")
        joined = " ".join(m["content"].lower() for m in transcript if m["role"] == "assistant")
        if appt is not None and "canary" not in joined:
            failures.append("branch spoken aloud does not match branch booked")
        return failures

    return Scenario(
        name="en_earliest_cross_branch", language="en", phone="4477001003",
        turns=[
            "What's the earliest general dentistry slot you have today, any branch?",
            "Book it. Chloe Davis, born 9 September 1995.",
            "Yes.",
        ],
        setup=setup, check=check,
    )


def en_returning_patient() -> Scenario:
    async def setup(db):
        db.add(Patient(name="David Evans", phone="4477001004", dob=date(1975, 7, 20)))
        await db.commit()

    async def check(db, transcript, tool_log):
        failures = []
        if _tool_count(tool_log, "get_caller_context") < 1:
            failures.append("never pulled caller context")
        assistant = " ".join(m["content"].lower() for m in transcript
                             if m["role"] == "assistant")
        if "david" not in assistant:
            failures.append("did not recognize returning patient by name")
        if await _booked(db, "4477001004") is None:
            failures.append("no appointment booked")
        return failures

    return Scenario(
        name="en_returning_patient", language="en", phone="4477001004",
        turns=[
            "Hello, I'd like to book a check-up sometime this week, afternoon if possible.",
            "Yes it's David. The first afternoon slot is fine.",
            "Yes, confirm.",
        ],
        setup=setup, check=check,
    )


def en_family_line() -> Scenario:
    async def setup(db):
        db.add(Patient(name="Maria Lopez", phone="4477001005", dob=date(1970, 2, 2)))
        db.add(Patient(name="Sofia Lopez", phone="4477001005", dob=date(2005, 8, 14)))
        await db.commit()

    async def check(db, transcript, tool_log):
        failures = []
        first_reply = next((m["content"].lower() for m in transcript
                            if m["role"] == "assistant"), "")
        if not any(w in first_reply for w in ("who", "which", "maria", "sofia")):
            failures.append("family line: did not disambiguate which patient is calling")
        appts = [a for a in (await db.execute(select(Appointment))).scalars()]
        sofia = (await db.execute(select(Patient).where(Patient.name == "Sofia Lopez"))
                 ).scalars().first()
        if not any(a.patient_id == sofia.id and a.status == "booked" for a in appts):
            failures.append("booked for the wrong family member (or not at all)")
        return failures

    return Scenario(
        name="en_family_line_disambiguation", language="en", phone="4477001005",
        turns=[
            "Hi, I want to book a hygiene appointment.",
            "It's for Sofia. Any Thursday morning is great.",
            "Yes, the first one. Book it.",
            "Yes.",
        ],
        setup=setup, check=check,
    )


def en_dropped_call_resume() -> Scenario:
    async def setup(db):
        db.add(Patient(name="George Hall", phone="4477001006", dob=date(1982, 11, 30)))
        db.add(CallSession(
            sid="dropped-1", caller="+4477001006", state="vapi", outcome=None,
            started_at=clock.now() - timedelta(minutes=10),
            data={"summary": "George Hall was booking a General Dentistry check-up, "
                             "preferred tomorrow morning; call dropped before a slot was chosen."},
        ))
        await db.commit()

    async def check(db, transcript, tool_log):
        failures = []
        assistant_turns = [m["content"].lower() for m in transcript if m["role"] == "assistant"]
        if not any(("cut off" in t) or ("dropped" in t) or ("sorry" in t and "george" in t)
                   for t in assistant_turns):
            failures.append("did not acknowledge the dropped call")
        joined = " ".join(assistant_turns)
        if "name" in joined and "george" not in joined:
            failures.append("re-asked for the name instead of resuming")
        if await _booked(db, "4477001006") is None:
            failures.append("did not complete the interrupted booking")
        return failures

    return Scenario(
        name="en_dropped_call_resume", language="en", phone="4477001006",
        turns=[
            "Hi, I think we got disconnected a few minutes ago.",
            "Yes exactly, tomorrow morning. The earliest is fine.",
            "Yes, book it.",
        ],
        setup=setup, check=check,
    )


def en_stale_availability() -> Scenario:
    """Between turn 1 and turn 2 someone else takes slots; the agent must
    re-run the availability tool, not reuse the stale result in context."""
    state: dict = {}

    async def hook(db):
        # book out EVERY slot the agent may have just offered (tomorrow, Dr Gupta)
        from packages.scheduler.availability import free_slots
        rival = Patient(name="Rival Booker", phone="4400000001", dob=date(1980, 1, 1))
        db.add(rival)
        await db.flush()
        day = clock.today() + timedelta(days=1)
        for slot in (await free_slots(db, 4, day))[:3]:
            db.add(Appointment(patient_id=rival.id, doctor_id=4, branch_id=2,
                               starts_at=slot))
        await db.commit()
        state["taken"] = True

    async def check(db, transcript, tool_log):
        failures = []
        if _tool_count(tool_log, "check_availability") + _tool_count(
                tool_log, "find_earliest_slot") < 2:
            failures.append("answered second availability question from stale context "
                            "(no fresh tool call)")
        return failures

    return Scenario(
        name="en_stale_availability_recheck", language="en", phone="4477001007",
        turns=[
            "What does Dr Gupta at Canary Wharf have free tomorrow?",
            "Sorry, someone may have just taken those — can you check tomorrow again right now?",
        ],
        hooks={1: hook}, check=check,
    )


def en_human_handoff() -> Scenario:
    async def check(db, transcript, tool_log):
        failures = []
        if _tool_count(tool_log, "log_follow_up") < 1:
            failures.append("did not log the human follow-up request")
        joined = " ".join(m["content"].lower() for m in transcript
                          if m["role"] == "assistant")
        if "transfer" in joined and "call" not in joined:
            failures.append("implied a live transfer instead of a callback")
        follow = (await db.execute(select(AuditLog).where(
            AuditLog.action == "follow_up"))).scalars().first()
        if follow is None:
            failures.append("no follow_up audit row written")
        return failures

    return Scenario(
        name="en_bot_honesty_and_handoff", language="en", phone="4477001008",
        turns=[
            "Am I talking to a robot? I'd rather speak to a real person about a billing mistake.",
            "Yes please, have someone call me back about the billing issue.",
        ],
        check=check,
    )


def hi_booking() -> Scenario:
    async def check(db, transcript, tool_log):
        failures = []
        if await _booked(db, "9198001001") is None:
            failures.append("no appointment booked from Hindi conversation")
        assistant = " ".join(m["content"] for m in transcript if m["role"] == "assistant")
        if not any("\u0900" <= ch <= "\u097f" for ch in assistant):
            failures.append("replied in English to a pure-Hindi caller (language drift)")
        return failures

    return Scenario(
        name="hi_full_booking", language="hi", phone="9198001001",
        turns=[
            "नमस्ते, मुझे दाँतों की सफाई के लिए अपॉइंटमेंट चाहिए, गुरुवार सुबह का कोई भी समय ठीक है।",
            "पहला वाला ठीक है। मेरा नाम रोहन वर्मा है, जन्मतिथि पाँच जनवरी उन्नीस सौ नब्बे।",
            "हाँ, बुक कर दीजिए।",
        ],
        check=check,
    )


def hi_code_switch() -> Scenario:
    async def check(db, transcript, tool_log):
        failures = []
        if await _booked(db, "9198001002") is None:
            failures.append("no appointment booked from Hinglish conversation")
        appt = await _booked(db, "9198001002")
        if appt and appt.starts_at.time() < time(16, 0):
            failures.append("ignored 'after 4:30' constraint")
        return failures

    return Scenario(
        name="hi_code_switch_booking", language="hi", phone="9198001002",
        turns=[
            "Hello, mujhe ek dentist appointment book karni hai — afternoon after I get off work, around saade chaar baje.",
            "Haan koi bhi din chalega is week. Naam hai Kavita Rao, DOB is 22 June 1993.",
            "Dhyaan rahe — saade chaar matlab 4:30 PM ke BAAD. Jo pehla slot ho after 4:30, wahi book kar do.",
            "Haan, confirm.",
        ],
        check=check,
    )


ALL_SCENARIOS: list[Callable[[], Scenario]] = [
    en_fuzzy_date, en_weekday_preference, en_earliest_cross_branch,
    en_returning_patient, en_family_line, en_dropped_call_resume,
    en_stale_availability, en_human_handoff, hi_booking, hi_code_switch,
]
