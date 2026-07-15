"""Slot validation edge cases."""
from datetime import date, timedelta

from packages.conversation import slots


def test_phone():
    assert slots.validate_phone("+91 98765-43210")[0] == "919876543210"
    assert slots.validate_phone("123")[0] is None
    assert slots.validate_phone("call me maybe")[0] is None


def test_dob():
    assert slots.validate_dob("1990-03-05")[0] == "1990-03-05"
    assert slots.validate_dob("2099-01-01")[0] is None  # future
    assert slots.validate_dob("not a date")[0] is None
    assert slots.validate_dob("1800-01-01")[0] is None  # too old


def test_date():
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert slots.validate_date(tomorrow)[0] == tomorrow
    assert slots.validate_date("2020-01-01")[0] is None  # past
    far = (date.today() + timedelta(days=365)).isoformat()
    assert slots.validate_date(far)[0] is None  # beyond booking horizon


def test_time_and_name():
    assert slots.validate_time("14:30")[0] == "14:30"
    assert slots.validate_time("half past two")[0] is None
    assert slots.validate_name("Ravi Kumar")[0] == "Ravi Kumar"
    assert slots.validate_name("x")[0] is None
