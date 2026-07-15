"""REST API + voice webhook integration tests."""
from datetime import date, timedelta

import httpx
import pytest


@pytest.fixture
async def client(db):
    from apps.backend.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def auth(client) -> dict:
    resp = await client.post("/api/auth/login", json={"email": "admin@test", "password": "pw"})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_login_rejects_bad_password(client):
    resp = await client.post("/api/auth/login", json={"email": "admin@test", "password": "no"})
    assert resp.status_code == 401


async def test_endpoints_require_auth(client):
    for path in ["/api/patients", "/api/appointments", "/api/calls", "/api/metrics"]:
        assert (await client.get(path)).status_code == 401


async def test_health(client):
    assert (await client.get("/api/health/live")).json() == {"status": "ok"}
    assert (await client.get("/api/health/ready")).status_code == 200


async def test_booking_via_api_and_conflict(client):
    headers = await auth(client)
    day = date.today() + timedelta(days=1)
    while day.weekday() > 4:
        day += timedelta(days=1)
    starts = f"{day.isoformat()}T10:00:00"

    body = {"patient_id": 1, "doctor_id": 1, "starts_at": starts}
    resp = await client.post("/api/appointments", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    appt_id = resp.json()["id"]

    # double booking blocked
    assert (await client.post("/api/appointments", json=body, headers=headers)).status_code == 409

    # availability excludes the taken slot
    avail = await client.get(f"/api/doctors/1/availability?day={day.isoformat()}",
                             headers=headers)
    assert not any(s.startswith(f"{day.isoformat()}T10:00") for s in avail.json()["slots"])

    # cancel frees it
    assert (await client.delete(f"/api/appointments/{appt_id}",
                                headers=headers)).status_code == 200
    resp = await client.post("/api/appointments", json=body, headers=headers)
    assert resp.status_code == 201


async def test_patient_crud_and_family_line(client):
    headers = await auth(client)
    body = {"name": "New Patient", "phone": "9000000001", "dob": "1992-01-01"}
    assert (await client.post("/api/patients", json=body, headers=headers)).status_code == 201
    # family lines: a second patient may share the same number
    body2 = {"name": "Family Member", "phone": "9000000001", "dob": "2010-04-04"}
    assert (await client.post("/api/patients", json=body2, headers=headers)).status_code == 201
    resp = await client.get("/api/patients?q=9000000001", headers=headers)
    assert len(resp.json()) == 2


async def test_voice_webhook_greets_and_logs_call(client):
    resp = await client.post("/api/voice", data={"CallSid": "CAtest", "From": "+911111111111"})
    assert resp.status_code == 200
    assert "<Gather" in resp.text and "how can i help" in resp.text.lower()

    headers = await auth(client)
    calls = (await client.get("/api/calls", headers=headers)).json()
    assert any(c["sid"] == "CAtest" for c in calls)


async def test_voice_webhook_rejects_missing_sid(client):
    assert (await client.post("/api/voice", data={})).status_code == 400
