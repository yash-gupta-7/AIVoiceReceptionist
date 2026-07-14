"""Intent registry: slots required, confidence gate, and how each intent is handled."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Intent:
    name: str
    description: str
    slots: list[str] = field(default_factory=list)
    threshold: float = 0.6
    kind: str = "flow"  # flow | faq | control


INTENTS: dict[str, Intent] = {
    i.name: i
    for i in [
        Intent("book", "Book a new appointment",
               ["name", "phone", "dob", "doctor", "date", "time"]),
        Intent("cancel", "Cancel an existing appointment", ["phone", "dob"]),
        Intent("reschedule", "Move an existing appointment",
               ["phone", "dob", "date", "time"]),
        Intent("hours", "Clinic opening hours", kind="faq"),
        Intent("location", "Clinic address / directions", kind="faq"),
        Intent("insurance", "Accepted insurance plans", kind="faq"),
        Intent("pricing", "Consultation pricing", kind="faq"),
        Intent("doctor_info", "Doctor availability / specialties", kind="faq"),
        Intent("human", "Transfer to a human", kind="control", threshold=0.5),
        Intent("emergency", "Medical emergency", kind="control", threshold=0.3),
        Intent("complaint", "Complaint — route to a human", kind="control"),
        Intent("greeting", "Small talk / greeting", kind="control", threshold=0.5),
        Intent("repeat", "Repeat last response", kind="control", threshold=0.5),
        Intent("goodbye", "End the call", kind="control", threshold=0.5),
        Intent("unknown", "Could not classify", kind="control", threshold=0.0),
    ]
}

# Deterministic emergency pre-check — never gated on the LLM.
EMERGENCY_WORDS = (
    "emergency", "chest pain", "can't breathe", "cannot breathe", "unconscious",
    "bleeding badly", "heart attack", "stroke", "suicide", "overdose",
)
