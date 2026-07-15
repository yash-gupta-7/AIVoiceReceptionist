"""Seed clinic data + admin user. Idempotent — safe to run on every start.

Two-branch London dental clinic (Europe/London, GBP). Swap the rows below for
your sourced clinic's real doctors/branches — this file is the single place
clinic data lives.
"""
import asyncio
from datetime import time

from sqlalchemy import select

from apps.backend.auth import hash_password
from packages.database.models import Branch, Doctor, Schedule, User
from packages.database.session import get_sessionmaker
from packages.shared.config import get_settings

BRANCHES = [
    {
        "name": "City Dental Care — Bank",
        "address": "24 King William Street, London EC4R 9AT",
        "phone": "+442071234500",
        "info": {
            "hours": "Mon-Fri 8am-6pm, Sat 9am-1pm",
            "insurance": "We accept Bupa, Denplan, and self-pay.",
            "pricing": "Check-up £65, hygiene £85, orthodontic consultation £120. Prices in pounds sterling.",
            "cancellation_policy": "Cancellations or reschedules within 24 hours of the appointment incur a £25 fee.",
        },
    },
    {
        "name": "City Dental Care — Canary Wharf",
        "address": "15 Cabot Square, London E14 4QT",
        "phone": "+442071234501",
        "info": {
            "hours": "Mon-Sat 9am-5pm",
            "insurance": "We accept Bupa, Denplan, and self-pay.",
            "pricing": "Check-up £65, hygiene £85, orthodontic consultation £120. Prices in pounds sterling.",
            "cancellation_policy": "Cancellations or reschedules within 24 hours of the appointment incur a £25 fee.",
        },
    },
]

# (name, department, branch index, weekdays, start, end, slot_min, buffer_min)
DOCTORS = [
    ("Dr. Sarah Mistry", "General Dentistry", 0, range(5), time(8, 0), time(16, 0), 30, 0),
    ("Dr. James O'Connor", "Orthodontics", 0, [0, 2, 4], time(9, 0), time(17, 0), 45, 15),
    ("Dr. Priya Nair", "Hygiene", 0, [1, 3, 5], time(9, 0), time(13, 0), 30, 0),
    # evening hours at the commuter branch — callers booking "after work" need >17:00
    ("Dr. Anil Gupta", "General Dentistry", 1, range(6), time(9, 0), time(18, 0), 30, 10),
    ("Dr. Emma Clarke", "Orthodontics", 1, [1, 3], time(10, 0), time(16, 0), 45, 15),
]


async def seed() -> None:
    s = get_settings()
    async with get_sessionmaker()() as db:
        if (await db.execute(select(Branch).limit(1))).scalar_one_or_none():
            print("already seeded")
            return

        branch_ids = []
        for spec in BRANCHES:
            branch = Branch(**spec)
            db.add(branch)
            await db.flush()
            branch_ids.append(branch.id)

        for name, dept, b_idx, weekdays, start, end, slot, buffer in DOCTORS:
            doctor = Doctor(name=name, department=dept, branch_id=branch_ids[b_idx])
            db.add(doctor)
            await db.flush()
            for weekday in weekdays:
                db.add(Schedule(doctor_id=doctor.id, weekday=weekday, start=start,
                                end=end, slot_minutes=slot, buffer_minutes=buffer))

        db.add(User(email=s.admin_email, password_hash=hash_password(s.admin_password),
                    role="admin"))
        await db.commit()
        print("seeded")


if __name__ == "__main__":
    asyncio.run(seed())
