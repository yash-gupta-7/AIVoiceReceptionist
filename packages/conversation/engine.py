"""Deterministic conversation state machine.

States: greeting -> intent -> collect -> confirm -> closing (or transfer).
The machine drives the flow; the LLM is only used for two narrow jobs —
intent/language classification and slot extraction — plus FAQ phrasing.
"""
import logging
import time as time_mod
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.conversation.intents import EMERGENCY_WORDS, INTENTS
from packages.conversation.slots import SLOTS
from packages.conversation import tools
from packages.database.models import AuditLog, CallSession, Doctor, Message
from packages.llm.provider import LLMError, LLMProvider
from packages.shared import clock
from packages.shared.config import get_settings
from packages.shared.logging import log
from prompts import load_prompt, render_prompt

logger = logging.getLogger("engine")

YES_WORDS = {"yes", "yeah", "yep", "correct", "right", "confirm", "sure", "ok", "okay",
             "haan", "ha", "si", "oui"}
NO_WORDS = {"no", "nope", "wrong", "change", "nahi", "nah"}

CLARIFY = "Sorry, I didn't quite get that. You can book, cancel, or reschedule an appointment, or ask about the clinic."
TRANSFER_SAY = "Let me connect you with our staff. One moment please."
EMERGENCY_SAY = "If this is a medical emergency, please hang up and dial your local emergency number right away."


@dataclass
class Reply:
    say: str
    action: str = "gather"  # gather | hangup | transfer


class ConversationEngine:
    def __init__(self, llm: LLMProvider) -> None:
        self.llm = llm
        self.settings = get_settings()

    async def handle(self, db: AsyncSession, call: CallSession, text: str | None) -> Reply:
        """Advance the state machine one caller turn. Persists messages and state."""
        started = time_mod.monotonic()
        data = dict(call.data or {})
        try:
            reply = await self._step(db, call, data, (text or "").strip())
        except Exception:
            logger.exception("engine failure")
            reply = Reply(TRANSFER_SAY, "transfer")  # never crash mid-call
        latency = int((time_mod.monotonic() - started) * 1000)
        if text:
            db.add(Message(call_id=call.id, role="caller", text=text,
                           intent=data.get("intent"), latency_ms=latency))
        db.add(Message(call_id=call.id, role="assistant", text=reply.say))
        data["last_say"] = reply.say
        call.data = data
        if reply.action != "gather":
            call.ended_at = clock.now()
            call.outcome = call.outcome or reply.action
        await db.commit()
        log(logger, "turn", state=call.state, intent=data.get("intent"),
            latency_ms=latency, action=reply.action)
        return reply

    async def _step(self, db: AsyncSession, call: CallSession, data: dict, text: str) -> Reply:
        lowered = text.lower()
        if any(w in lowered for w in EMERGENCY_WORDS):
            call.state = "closing"
            return Reply(EMERGENCY_SAY, "hangup")

        if call.state == "greeting" or not text:
            call.state = "intent"
            return Reply(
                f"Thank you for calling {self.settings.clinic_name}. How can I help you today?"
            )

        if call.state == "intent":
            return await self._route_intent(db, call, data, text)
        if call.state == "collect":
            return await self._collect(db, call, data, text)
        if call.state == "confirm":
            return await self._confirm(db, call, data, lowered)
        call.state = "intent"
        return await self._route_intent(db, call, data, text)

    async def _classify(self, call: CallSession, text: str) -> tuple[str, float]:
        try:
            result = await self.llm.complete_json(
                [{"role": "system", "content": load_prompt("intent")},
                 {"role": "user", "content": text}]
            )
        except LLMError:
            return "unknown", 0.0
        intent = str(result.get("intent", "unknown"))
        confidence = float(result.get("confidence", 0))
        language = str(result.get("language", "")) or call.language
        call.language = language[:8]
        if intent not in INTENTS or confidence < INTENTS[intent].threshold:
            return "unknown", confidence
        return intent, confidence

    async def _route_intent(self, db, call: CallSession, data: dict, text: str) -> Reply:
        intent, confidence = await self._classify(call, text)
        data["intent"] = intent
        log(logger, "intent", intent=intent, confidence=confidence)
        spec = INTENTS[intent]

        if intent == "goodbye":
            call.state = "closing"
            call.outcome = call.outcome or "completed"
            return Reply("Thanks for calling. Take care!", "hangup")
        if intent in ("human", "complaint"):
            return Reply(TRANSFER_SAY, "transfer")
        if intent == "repeat":
            return Reply(data.get("last_say") or CLARIFY)
        if intent == "greeting":
            return Reply("Hello! I can help you book, cancel, or reschedule an appointment. What would you like?")
        if spec.kind == "faq":
            return await self._faq(db, call, text)
        if spec.kind == "flow":
            data.update({"slots": {}, "retries": 0})
            call.state = "collect"
            return await self._collect(db, call, data, text)

        # unknown / low confidence
        data["retries"] = data.get("retries", 0) + 1
        if data["retries"] >= self.settings.slot_retry_limit:
            return Reply(TRANSFER_SAY, "transfer")
        return Reply(CLARIFY)

    async def _faq(self, db, call: CallSession, text: str) -> Reply:
        branch = await tools.lookup_branch(db)
        doctors = ", ".join(
            f"{d.name} ({d.department})"
            for d in (await db.execute(select(Doctor))).scalars()
        )
        context = (
            f"Clinic: {self.settings.clinic_name}. Address: {branch.address if branch else 'unknown'}. "
            f"Phone: {branch.phone if branch else 'unknown'}. Info: {branch.info if branch else {}}. "
            f"Doctors: {doctors}."
        )
        try:
            answer = await self.llm.complete(
                [{"role": "system",
                  "content": render_prompt("faq", language=call.language, context=context)},
                 {"role": "user", "content": text}]
            )
        except LLMError:
            answer = "I'm having trouble looking that up right now. Would you like me to connect you with our staff?"
        return Reply(f"{answer} Anything else I can help with?")

    async def _collect(self, db, call: CallSession, data: dict, text: str) -> Reply:
        needed = INTENTS[data["intent"]].slots
        slots: dict = data.setdefault("slots", {})
        missing = [s for s in needed if s not in slots]

        extracted: dict = {}
        if text and missing:
            try:
                extracted = await self.llm.complete_json(
                    [{"role": "system", "content": render_prompt(
                        "slots", slot_list=", ".join(missing), today=clock.today().isoformat())},
                     {"role": "user", "content": text}]
                )
            except LLMError:
                extracted = {}

        error_say: str | None = None
        for slot_name in needed:
            if slot_name in extracted and slot_name not in slots:
                value, error = SLOTS[slot_name][0](extracted[slot_name])
                if error:
                    error_say = error_say or error
                else:
                    slots[slot_name] = value

        # Resolve doctor slot against the roster as soon as we have it.
        if "doctor" in slots and "doctor_id" not in slots:
            doctor = await tools.lookup_doctor(db, slots["doctor"])
            if doctor is None:
                del slots["doctor"]
                error_say = "I couldn't find that doctor. We have general medicine, dental, and pediatrics — who would you like?"
            else:
                slots["doctor_id"] = doctor.id
                slots["doctor"] = doctor.name

        missing = [s for s in needed if s not in slots]
        if error_say or (missing and not extracted and text):
            data["retries"] = data.get("retries", 0) + 1
            if data["retries"] > self.settings.slot_retry_limit:
                return Reply(TRANSFER_SAY, "transfer")
        else:
            data["retries"] = 0

        if error_say:
            return Reply(error_say)
        if missing:
            return Reply(SLOTS[missing[0]][1])
        return await self._prepare_confirmation(db, call, data)

    async def _prepare_confirmation(self, db, call: CallSession, data: dict) -> Reply:
        slots = data["slots"]
        intent = data["intent"]

        if intent == "book":
            when = datetime.fromisoformat(f"{slots['date']}T{slots['time']}:00")
            open_slots = await tools.lookup_schedule(db, slots["doctor_id"], when.date())
            if when not in open_slots:
                return self._offer_alternatives(call, data, open_slots)
            call.state = "confirm"
            return Reply(
                f"To confirm: {slots['name']}, with {slots['doctor']} on "
                f"{when.strftime('%A %B %d at %I:%M %p')}. Shall I book it?"
            )

        # cancel / reschedule: identify the patient and their upcoming appointment first
        patient = await tools.find_patient(
            db, tools.FindPatientIn(phone=slots["phone"], dob=date.fromisoformat(slots["dob"]))
        )
        if patient is None:
            return Reply(
                "I couldn't find a patient matching that phone number and date of birth. "
                + TRANSFER_SAY, "transfer",
            )
        appt = await tools.find_upcoming_appointment(db, patient.id)
        if appt is None:
            call.state = "intent"
            return Reply("I don't see any upcoming appointments for you. Anything else I can help with?")
        data["appointment_id"] = appt.id
        data["slots"]["doctor_id"] = appt.doctor_id
        when = appt.starts_at.strftime("%A %B %d at %I:%M %p")

        if intent == "cancel":
            call.state = "confirm"
            return Reply(f"You have an appointment on {when}. Should I cancel it?")

        new_when = datetime.fromisoformat(f"{slots['date']}T{slots['time']}:00")
        open_slots = await tools.lookup_schedule(db, appt.doctor_id, new_when.date())
        if new_when not in open_slots:
            return self._offer_alternatives(call, data, open_slots)
        call.state = "confirm"
        return Reply(
            f"Move your appointment from {when} to "
            f"{new_when.strftime('%A %B %d at %I:%M %p')} — is that right?"
        )

    def _offer_alternatives(self, call: CallSession, data: dict, open_slots: list) -> Reply:
        data["slots"].pop("time", None)
        call.state = "collect"
        if not open_slots:
            data["slots"].pop("date", None)
            return Reply("There's no availability that day. Could you pick another date?")
        options = ", ".join(s.strftime("%I:%M %p").lstrip("0") for s in open_slots[:3])
        return Reply(f"That time is taken. I have {options} available — which works?")

    async def _confirm(self, db, call: CallSession, data: dict, lowered: str) -> Reply:
        words = set(lowered.replace(",", " ").replace(".", " ").split())
        if words & NO_WORDS:
            call.state = "collect"
            data["slots"].pop("time", None)
            data["slots"].pop("date", None)
            return Reply("No problem. What date and time would you like instead?")
        if not words & YES_WORDS:
            return Reply("Sorry, was that a yes or a no?")
        return await self._commit(db, call, data)

    async def _commit(self, db, call: CallSession, data: dict) -> Reply:
        slots = data["slots"]
        intent = data["intent"]
        call.state = "intent"
        try:
            if intent == "book":
                patient = await tools.find_patient(db, tools.FindPatientIn(phone=slots["phone"]))
                if patient is None:
                    patient = await tools.run_tool(
                        db, call.id, "create_patient",
                        tools.create_patient(db, tools.CreatePatientIn(
                            name=slots["name"], phone=slots["phone"],
                            dob=date.fromisoformat(slots["dob"]))),
                        {"phone": slots["phone"]},
                    )
                doctor = await db.get(Doctor, slots["doctor_id"])
                when = datetime.fromisoformat(f"{slots['date']}T{slots['time']}:00")
                appt = await tools.run_tool(
                    db, call.id, "book_appointment",
                    tools.book_appointment(db, tools.BookIn(
                        patient_id=patient.id, doctor_id=doctor.id,
                        branch_id=doctor.branch_id, starts_at=when)),
                    {"doctor_id": doctor.id, "starts_at": when.isoformat()},
                )
                say = (f"You're all set for {when.strftime('%A %B %d at %I:%M %p')}. "
                       f"Your confirmation number is {appt.id}. Anything else?")
                action = "booked"
            elif intent == "cancel":
                await tools.run_tool(
                    db, call.id, "cancel_appointment",
                    tools.cancel_appointment(db, tools.CancelIn(
                        appointment_id=data["appointment_id"])),
                    {"appointment_id": data["appointment_id"]},
                )
                say = "Your appointment is cancelled. Anything else I can help with?"
                action = "cancelled"
            else:  # reschedule
                when = datetime.fromisoformat(f"{slots['date']}T{slots['time']}:00")
                await tools.run_tool(
                    db, call.id, "reschedule_appointment",
                    tools.reschedule_appointment(db, tools.RescheduleIn(
                        appointment_id=data["appointment_id"], starts_at=when)),
                    {"appointment_id": data["appointment_id"], "starts_at": when.isoformat()},
                )
                say = (f"Done — you're rescheduled to {when.strftime('%A %B %d at %I:%M %p')}. "
                       "Anything else?")
                action = "rescheduled"
        except tools.ToolError as exc:
            if str(exc) == "slot_taken":
                call.state = "collect"
                data["slots"].pop("time", None)
                return Reply("Ah, that slot was just taken. What other time works for you?")
            return Reply("Something went wrong on my end. " + TRANSFER_SAY, "transfer")

        call.outcome = action
        db.add(AuditLog(actor="voice-agent", action=action,
                        detail={"call_sid": call.sid, **{k: str(v) for k, v in slots.items()}}))
        return Reply(say)
