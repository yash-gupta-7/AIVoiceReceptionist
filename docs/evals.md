# Eval harness — what it measures and why

Run: `python -m evals.run` (needs an LLM key in `.env`; results land in
`evals/results.json` and the summary prints as markdown). Re-runnable from a
clean clone; each scenario gets a **fresh seeded database**, so runs are
independent and order-insensitive.

## Design

The harness drives the **same system prompt and the same 9 tool schemas the
live Vapi assistant uses**, with an LLM doing real function-calling against
the real tool implementations and a real (SQLite) database. Scenarios are
multi-turn scripted conversations, several with **mid-conversation hooks**
that mutate live data between turns — e.g. a rival caller taking the offered
slots — because the required failure modes are about state, not single-turn
accuracy.

Checks inspect **the database and the tool-call log**, not just the
transcript: "did an appointment row actually get created for Sofia (not
Maria)", "was `check_availability` re-invoked after the data changed",
"was a follow_up audit row written". A polite transcript with no booking
fails.

## Dimensions and why we picked them

| Metric | Why it matters |
|---|---|
| **Pass/fail per scenario** | Each scenario maps 1:1 to a required failure mode from production clinics (returning patient, family line, dropped-call resume, stale availability, cross-branch earliest, honesty/handoff, fuzzy times, EN/HI bookings). |
| **Turns to completion** | The assignment's core UX bar: book fast, never wander. Counted as caller turns consumed before the DB reflects the outcome. |
| **Redundant questions** (LLM-judged) | Re-asking something already said signals broken state tracking — the single most common receptionist-bot failure. A second model audits each transcript; -1 means the judge was unavailable. |
| **Per-language split (EN / HI)** | Blended metrics hide Hindi regressions behind English wins. Every aggregate is reported per language, never merged. |
| **Median LLM ms / tool ms** | The two latency components we control. Tool latency is ours end-to-end (target <50 ms); LLM latency drives voice snappiness and model choice. |

## Latency decomposition

Offline we can measure **LLM inference** and **tool execution** only. The
full voice loop is ASR + LLM + tool + TTS + network; ASR/TTS/network exist
only on the live call path, so they are collected from **Vapi's per-call
logs** (each call reports transcriber, model, and voice latencies) on the
live number, not synthesized here. That's a deliberate honesty choice — see
below.

## Where this harness gives false confidence

1. **No audio.** Text-in/text-out skips ASR entirely. Hindi/Hinglish speech
   recognition quality — accents, numbers spoken in Hindi, names — is the
   riskiest untested layer. Validate on the live number.
2. **The eval LLM ≠ the live LLM.** The harness uses our configured provider
   (Groq Llama 3.3 by default); the live assistant runs GPT-4o on Vapi. Same
   prompt and tools, different model — treat harness passes as logic/prompt
   validation, not a guarantee for the production model.
3. **Scripted callers are cooperative.** Real callers interrupt, mumble, and
   change their minds mid-utterance. Turn-taking and barge-in quality are
   platform behaviors invisible here.
4. **The redundancy judge is itself an LLM** and can miscount; it's a signal,
   not ground truth.
5. **Timing skew.** Scenario dates are computed relative to "today", so a run
   at 23:59 local can have same-day scenarios legitimately find no slots.

## Adding a scenario

Add a factory in `evals/scenarios.py` returning a `Scenario(name, language,
phone, turns, setup?, hooks?, check?)` and append it to `ALL_SCENARIOS`. Keep
the check DB-backed.
