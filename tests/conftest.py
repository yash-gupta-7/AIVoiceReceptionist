"""Shared fixtures: in-memory SQLite DB, seeded clinic, scripted LLM."""
import os

os.environ.update(
    DATABASE_URL="sqlite+aiosqlite://",
    JWT_SECRET="test-secret",
    LLM_PROVIDER="fake",
    TWILIO_AUTH_TOKEN="",
    VAPI_SECRET="",  # webhook auth off in tests
    PUBLIC_URL="http://localhost:9",  # PMS write-back fails fast -> 'failed' path
    CLINIKO_API_KEY="",  # never let tests write to a real PMS
)

import json  # noqa: E402
from datetime import date, time  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import packages.database.session as db_session  # noqa: E402
from apps.backend.auth import hash_password  # noqa: E402
from packages.database.models import Base, Branch, Doctor, Patient, Schedule, User  # noqa: E402
from packages.llm.provider import LLMProvider  # noqa: E402


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    # point the app's session factory at this test engine
    db_session._engine = engine
    db_session._sessionmaker = maker
    async with maker() as session:
        branch = Branch(name="Main", address="12 MG Road", phone="+911234567890",
                        info={"hours": "Mon-Fri 9-5"})
        session.add(branch)
        await session.flush()
        doctor = Doctor(name="Dr. Asha Rao", department="General Medicine",
                        branch_id=branch.id)
        session.add(doctor)
        await session.flush()
        for weekday in range(7):
            session.add(Schedule(doctor_id=doctor.id, weekday=weekday,
                                 start=time(9, 0), end=time(17, 0), slot_minutes=30))
        session.add(Patient(name="Ravi Kumar", phone="9876543210", dob=date(1990, 3, 5)))
        session.add(User(email="admin@test", password_hash=hash_password("pw"), role="admin"))
        await session.commit()
        yield session
    await engine.dispose()


class ScriptedLLM(LLMProvider):
    """Returns queued responses in order; lets tests script exact model output."""

    def __init__(self, responses: list) -> None:
        self.responses = list(responses)

    async def complete(self, messages: list[dict], json_mode: bool = False) -> str:
        item = self.responses.pop(0)
        return item if isinstance(item, str) else json.dumps(item)
