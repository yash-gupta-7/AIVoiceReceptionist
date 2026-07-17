# Architecture

> **Note:** the primary voice path is now the Vapi assistant → `/api/vapi/webhook`
> → `packages/conversation/agent_tools.py` (see README). This document covers
> the shared backend and the legacy raw-Twilio state machine kept as a
> platform-independent fallback.

## Layers

```
Twilio webhook (apps/backend/voice.py)        — presentation, no business logic
Dashboard REST (apps/backend/routes.py)       — thin controllers, no SQL beyond queries
ConversationEngine (packages/conversation)    — deterministic state machine
Typed tools (packages/conversation/tools.py)  — all business writes, audited
Scheduler (packages/scheduler)                — availability math
Models (packages/database)                    — schema + constraints
```

The state machine drives the conversation; prompts never encode flow. The LLM
has exactly three jobs: classify intent + language, extract slots to normalized
values, and phrase FAQ answers from supplied facts. All validation, routing,
retries, and confirmation logic is deterministic Python.

## State machine

```
greeting ──> intent ──┬─ faq intents ──────────> answer, stay in intent
                      ├─ book/cancel/reschedule > collect ──> confirm ──> commit ──> intent
                      ├─ human/complaint ───────> transfer
                      ├─ emergency (keyword, pre-LLM) ─> hangup with emergency message
                      └─ goodbye ───────────────> hangup
```

- Slot filling: LLM extracts, `slots.py` validates deterministically, engine
  asks for the first missing slot. 3 failed turns → transfer to human.
- Confirmation is an explicit yes/no gate before any write — the LLM cannot
  hallucinate a booking.
- Availability re-checked at confirm time; a partial unique index
  (`uq_doctor_slot` where status='booked') closes the race between two
  concurrent callers. Loser gets alternatives offered.

## Threat model notes

- **Prompt injection**: caller speech is labeled untrusted in every prompt;
  LLM output is only ever parsed as intent labels / slot values that are then
  validated deterministically. Free-form LLM text (FAQ) is generated only from
  DB facts, never from tool authority.
- **Caller spoofing**: cancel/reschedule requires phone + DOB match.
- **Replay/forgery**: Twilio webhook signatures validated (HMAC-SHA1).
- **API abuse**: JWT on all dashboard routes, in-memory rate limiting,
  Pydantic validation, SQLAlchemy parameterized queries.
- **Failures**: LLM calls retry with backoff then degrade to scripted
  responses; any unhandled engine error transfers to a human instead of
  crashing the call; tools have timeouts and are audit-logged.

## Memory

Per-call memory is the `call_sessions.data` JSON column (intent, slots,
retries, last response). Patient profile + appointment history are the
patient/appointment tables. Long-term summaries were deliberately skipped for
the MVP — add a summary job when transcripts need compaction.

## Deliberate MVP cuts (search `ponytail:` in code)

- Twilio `<Gather>` for STT/TTS instead of a streaming media pipeline —
  swap `packages/telephony` when sub-second latency matters.
- In-memory rate limiter — move to nginx/redis for multiple replicas.
- admin|staff role column instead of role/permission tables.
- SMS confirmations, analytics page, prompt-management UI: not built; the
  seams (tools registry, prompts/, dashboard router) are where they go.
