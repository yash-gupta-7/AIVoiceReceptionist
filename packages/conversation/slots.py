"""Slot definitions and deterministic validation. Never trust LLM extraction raw."""
import re
from datetime import date, datetime, timedelta

from packages.shared import clock

MAX_BOOKING_DAYS_AHEAD = 90


def _iso_date(value: str) -> date:
    return date.fromisoformat(str(value).strip())


def validate_name(value: str) -> tuple[str | None, str | None]:
    value = str(value).strip()
    if len(value) < 2 or not re.search(r"[a-zA-Zऀ-ॿ]", value):
        return None, "I didn't catch the name. Could you say the full name again?"
    return value, None


def validate_phone(value: str) -> tuple[str | None, str | None]:
    digits = re.sub(r"\D", "", str(value))
    if not 10 <= len(digits) <= 15:
        return None, "That phone number doesn't look complete. Could you repeat it digit by digit?"
    return digits, None


def validate_dob(value: str) -> tuple[str | None, str | None]:
    try:
        dob = _iso_date(value)
    except ValueError:
        return None, "Sorry, what is the date of birth? For example, March 5th, 1990."
    today = clock.today()
    if dob >= today or today.year - dob.year > 120:
        return None, "That date of birth doesn't seem right. Could you say it again?"
    return dob.isoformat(), None


def validate_date(value: str) -> tuple[str | None, str | None]:
    try:
        day = _iso_date(value)
    except ValueError:
        return None, "Which date would you like? For example, next Tuesday or July 20th."
    today = clock.today()
    if day < today:
        return None, "That date is in the past. Which upcoming date works for you?"
    if day > today + timedelta(days=MAX_BOOKING_DAYS_AHEAD):
        return None, "We can only book up to three months ahead. Could you pick an earlier date?"
    return day.isoformat(), None


def validate_time(value: str) -> tuple[str | None, str | None]:
    try:
        parsed = datetime.strptime(str(value).strip(), "%H:%M").time()
    except ValueError:
        return None, "What time would you prefer? For example, ten thirty in the morning."
    return parsed.strftime("%H:%M"), None


def validate_text(value: str) -> tuple[str | None, str | None]:
    value = str(value).strip()
    return (value, None) if value else (None, "Sorry, could you say that again?")


# slot name -> (validator, question asked when missing)
SLOTS: dict[str, tuple] = {
    "name": (validate_name, "May I have the patient's full name?"),
    "phone": (validate_phone, "What's the best phone number, digit by digit?"),
    "dob": (validate_dob, "And the patient's date of birth?"),
    "doctor": (validate_text, "Which doctor or department would you like to see?"),
    "date": (validate_date, "What date works for you?"),
    "time": (validate_time, "And what time of day do you prefer?"),
}
