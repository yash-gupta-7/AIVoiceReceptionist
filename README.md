# Voice AI Receptionist — City Dental Care

A bilingual (English/Hindi, mid-call code-switching) voice receptionist for a
two-branch London dental clinic. Callers book, reschedule, or cancel
appointments with no human involved. Built on **Vapi** for the voice layer and
a **FastAPI + PostgreSQL** backend that owns all scheduling truth, with an
idempotent PMS write-back and a re-runnable eval harness.

## Stack justification: why Vapi

| Dimension | Reasoning |
|---|---|
| **Tool-calling reliability** | Vapi's server-URL tools are plain OpenAI function schemas hitting our webhook with retries; the LLM never sees the DB. Retell's function calling is comparable but its state lives more in their agent graph; we wanted all state in *our* datastore so dropped-call resume and cross-branch logic are testable offline. |
| **Multilingual** | Vapi lets us pick Deepgram nova-2 `language: multi` for ASR (handles Hindi + English + code-switching in one stream) and ElevenLabs turbo v2.5 for TTS (natural Hindi). Bolna is strong for Indian languages but this clinic is UK-based and Vapi's UK PSTN + latency profile fit better. |
| **Latency** | Vapi streams ASR→LLM→TTS with configurable `startSpeakingPlan` (we use 0.4 s) and first-token TTS streaming. Our tool webhook adds one round-trip; every tool answers from indexed Postgres queries (<50 ms measured — see evals). |
| **Interruption/barge-in** | Native (`stopSpeakingPlan.numWords: 2`); agent state survives interruption because state lives in tools, not the utterance. |
| **Cost** | ~$0.07–0.13/min all-in at demo volume; no platform fee to keep an assistant configured. |

The trade-off vs. building raw telephony (Twilio Media Streams + Deepgram +
ElevenLabs): Vapi costs more per minute but removes the audio pipeline,
turn-taking, and barge-in engineering — exactly the parts that don't
differentiate a receptionist. Our earlier raw-Twilio implementation is still
in the repo (`apps/backend/voice.py`) as a fallback path.

## Architecture

```
Caller ⇄ Vapi (ASR: Deepgram multi · LLM: GPT-4o · TTS: ElevenLabs)
              │  tool calls + call events (x-vapi-secret)
              ▼
POST /api/vapi/webhook  ──►  packages/conversation/agent_tools.py
                                   │ get_caller_context   (returning patients, family lines,
                                   │                       dropped-call resume, callbacks)
                                   │ check_availability   (fuzzy: date / weekdays / time window)
                                   │ find_earliest_slot   (ALL practitioners, BOTH branches)
                                   │ book / cancel / reschedule (live re-check, conflict-safe)
                                   │ log_follow_up        (human callback queue)
                                   ▼
                        PostgreSQL (partial unique index kills double-booking at write time)
                                   │
                                   ▼
                        PMS write-back (idempotency-keyed; mock PMS built-in,
                        Cliniko REST when CLINIKO_API_KEY is set; failures
                        marked + retried, never block a booking)
```

Key behaviors, each enforced in code (not prompt hopes):

- **Double booking**: partial unique index `(doctor_id, starts_at) WHERE status='booked'`. A race loser gets alternatives, never a silent failure.
- **Stale availability**: tools are the only source of slots; the prompt mandates a fresh `check_availability` before quoting times, and the eval `en_stale_availability_recheck` verifies a second tool call happens.
- **Dropped calls / callbacks**: every call session persists; `get_caller_context` surfaces an unresolved call from the last 2 h (with summary) and missed-outbound context from the last 48 h.
- **Family lines**: `patients.phone` is intentionally NOT unique; context returns all patients on a number and the agent must disambiguate by name.
- **Full name always**: `book_appointment` rejects bookings without a patient name — anonymous bookings are impossible even if the prompt fails.
- **Timezone**: all storage is naive clinic-local time (`CLINIC_TZ=Europe/London`); "today" can never shift to "tomorrow" via UTC conversion. Prices are £.
- **Buffers**: per-schedule `buffer_minutes` keeps required gaps between slots.
- **Escalation honesty**: `log_follow_up` writes a callback queue entry; the agent never claims a live transfer.

## Running it

```bash
cp .env.example .env       # set GROK_API_KEY (any OpenAI-compatible LLM) — see below
docker compose up --build  # Postgres + backend (migrated & seeded) + dashboard on :8080
```

Local dev without Docker:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PYTHONPATH=. .venv/bin/alembic upgrade head && PYTHONPATH=. .venv/bin/python scripts/seed.py
PYTHONPATH=. .venv/bin/uvicorn apps.backend.main:app --port 8000
```

### Live number

**📞 +1 (985) 570-1191** — attached to the `city-dental-receptionist` Vapi
assistant. Works once the backend is publicly reachable (`PUBLIC_URL`) and
`scripts/vapi_setup.py` has been re-run against it.

### Going live on a phone number

1. Deploy the backend anywhere public (Docker image builds in CI), set `PUBLIC_URL` and a random `VAPI_SECRET` in `.env`.
2. `VAPI_API_KEY=… python scripts/vapi_setup.py` — creates/updates the assistant with the prompt (`prompts/v2/agent_system.txt`), all 9 tool schemas, multilingual ASR/TTS, and barge-in settings.
3. In the Vapi dashboard, attach a phone number to the `city-dental-receptionist` assistant. Done — the number is independently callable.

### PMS write-back (Cliniko)

With `CLINIKO_API_KEY` set, every confirmed booking creates a **real patient
and appointment in Cliniko** (correct UTC conversion from clinic-local time,
doctor/branch recorded in the appointment notes), reschedules move it, and
cancellations cancel it — all visible in the Cliniko calendar. Replays are
no-ops (guarded by the stored `cliniko_id`). A Cliniko trial has a single
practitioner/business, so all bookings land on that diary; with a full
account, map practitioners per doctor in `packages/pms/writeback.py`.

Without credentials, bookings write to the built-in mock PMS
(`POST /api/mock-pms/records`, idempotency-keyed, `?fail=1` simulates outage).
Failure behavior in both modes: booking succeeds locally (DB is source of
truth), appointment is marked `pms_status=failed`, audited, and re-driven by
`packages/pms/writeback.retry_failed`.

## Eval harness

```bash
python -m evals.run           # all scenarios; writes evals/results.json
python -m evals.run hi_       # just the Hindi ones
```

The harness runs the **same system prompt and tool schemas as the live
assistant** with an LLM doing real tool-calling against a fresh seeded DB per
scenario — multi-turn, scripted, with mid-conversation hooks that mutate live
data (e.g. a rival booking slots between turns). Scenarios cover: fuzzy time
references, weekday preferences, earliest-slot across branches, returning
patients, family-line disambiguation, dropped-call resume, stale-availability
re-check, bot-honesty/human handoff, and full Hindi + Hinglish bookings.

Reported per language (EN / HI, never blended): pass rate, turns-to-completion,
LLM-judged redundant-question count, median LLM latency, median tool latency.

Current results (Groq `gpt-oss-120b` as the eval model, full table in
`evals/results.json`):

| Language | Pass | Median turns to completion | Redundant questions | Median LLM | Median tool |
|---|---|---|---|---|---|
| English | 8/8 | 3.0 | 0 | 732 ms | 18 ms |
| Hindi (incl. Hinglish) | 2/2 | 3.5 | 0 | 880 ms | 44 ms |
See [docs/evals.md](docs/evals.md) for metric definitions, current numbers, and
**where the harness gives false confidence** (ASR/TTS are not exercised
offline — those are measured from Vapi call logs on the live number).

## Repository layout

```
apps/backend/            FastAPI: Vapi webhook, mock PMS, dashboard REST, JWT auth
apps/dashboard/          React ops dashboard (appointments, patients, call transcripts)
packages/conversation/   agent_tools.py (tool implementations) + tool_schema.py (single
                         source of truth for Vapi and evals) + legacy Twilio state machine
packages/scheduler/      availability search: date/weekday/window filters, buffers
packages/pms/            idempotent PMS write-back (mock + Cliniko)
packages/database/       SQLAlchemy models, Alembic migration
prompts/v2/              versioned agent system prompt (bilingual)
evals/                   scenario harness + results
scripts/                 seed.py (clinic data), vapi_setup.py (assistant provisioning)
tests/                   24 pytest cases (tools, webhook, PMS idempotency, API, engine)
```

## Known limitations

- The eval harness measures prompt+tool logic, not speech: Hindi ASR accuracy
  and TTS naturalness must be validated on the live number.
- Reschedule-fee logic (£25 inside 24 h) is prompt-driven from branch policy
  data, not enforced in a tool — a misbehaving LLM could mis-state it.
- Clinic data in `scripts/seed.py` is realistic but should be swapped for the
  sourced clinic before submission.
- Mock PMS is in-process; Cliniko adapter covers appointment create/cancel only.
- Outbound campaign calling isn't built; missed-outbound context is stored and
  recognized on callback, which covers the required scenario.

## Environment variables

All documented in [.env.example](.env.example).
