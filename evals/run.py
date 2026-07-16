"""Eval runner: fresh DB per scenario, simulated agent, per-language metrics.

    python -m evals.run              # all scenarios
    python -m evals.run en_family    # substring filter

Requires an LLM key in .env (LLM_PROVIDER=grok). Writes evals/results.json and
prints a per-language summary. Re-runnable from a clean clone:
    pip install -r requirements-dev.txt && python -m evals.run
"""
import asyncio
import json
import statistics
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from evals.agent_loop import SimulatedAgent
from evals.scenarios import ALL_SCENARIOS, Scenario
from packages.database.models import Base, Branch, Doctor, Schedule, User
from packages.llm.provider import LLMError, get_provider
from scripts.seed import BRANCHES, DOCTORS


async def fresh_db():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        branch_ids = []
        for spec in BRANCHES:
            b = Branch(**spec)
            db.add(b)
            await db.flush()
            branch_ids.append(b.id)
        for name, dept, b_idx, weekdays, start, end, slot, buffer in DOCTORS:
            d = Doctor(name=name, department=dept, branch_id=branch_ids[b_idx])
            db.add(d)
            await db.flush()
            for wd in weekdays:
                db.add(Schedule(doctor_id=d.id, weekday=wd, start=start, end=end,
                                slot_minutes=slot, buffer_minutes=buffer))
        db.add(User(email="eval@local", password_hash="x", role="staff"))
        await db.commit()
    return engine, maker


async def judge_redundancy(llm, transcript: list[dict]) -> int:
    """LLM-judged count of redundant questions (asking for info already given)."""
    convo = "\n".join(f"{m['role']}: {m['content']}" for m in transcript)
    try:
        result = await llm.complete_json([
            {"role": "system", "content":
                "You audit a receptionist transcript. Count how many times the assistant "
                "asked for information the caller had ALREADY provided earlier in the same "
                "conversation (any language). Respond JSON only: {\"redundant\": <int>}"},
            {"role": "user", "content": convo},
        ])
        return int(result.get("redundant", 0))
    except (LLMError, ValueError):
        return -1  # judge unavailable


async def run_scenario(llm, make: type[Scenario]) -> dict:
    scenario = make() if callable(make) else make
    engine, maker = await fresh_db()
    try:
        async with maker() as db:
            if scenario.setup:
                await scenario.setup(db)
            agent = SimulatedAgent(llm, db, scenario.phone)
            turns_used = 0
            for i, utterance in enumerate(scenario.turns):
                if i in scenario.hooks:
                    await scenario.hooks[i](db)
                await agent.turn(utterance)
                turns_used += 1
            failures = await scenario.check(db, agent.transcript(), agent.tool_log) \
                if scenario.check else []
            redundant = await judge_redundancy(llm, agent.transcript())
            return {
                "scenario": scenario.name,
                "language": scenario.language,
                "passed": not failures,
                "failures": failures,
                "turns_to_completion": turns_used,
                "redundant_questions": redundant,
                "llm_ms": agent.llm_ms,
                "tool_calls": agent.tool_log,
                "transcript": agent.transcript(),
            }
    except Exception as exc:
        return {"scenario": scenario.name, "language": scenario.language,
                "passed": False, "failures": [f"harness error: {exc}"],
                "turns_to_completion": None, "redundant_questions": -1,
                "llm_ms": [], "tool_calls": [], "transcript": []}
    finally:
        await engine.dispose()


def summarize(results: list[dict]) -> str:
    lines = ["", "| scenario | lang | pass | turns | redundant Qs | median LLM ms | median tool ms |",
             "|---|---|---|---|---|---|---|"]
    for r in results:
        tool_ms = [t["ms"] for t in r["tool_calls"]]
        lines.append(
            f"| {r['scenario']} | {r['language']} | {'✅' if r['passed'] else '❌'} "
            f"| {r['turns_to_completion']} | {r['redundant_questions']} "
            f"| {int(statistics.median(r['llm_ms'])) if r['llm_ms'] else '-'} "
            f"| {int(statistics.median(tool_ms)) if tool_ms else '-'} |")
        for f in r["failures"]:
            lines.append(f"|  | | ⤷ {f} | | | | |")
    for lang in ("en", "hi"):
        subset = [r for r in results if r["language"] == lang]
        if not subset:
            continue
        llm_all = [ms for r in subset for ms in r["llm_ms"]]
        tool_all = [t["ms"] for r in subset for t in r["tool_calls"]]
        turns = [r["turns_to_completion"] for r in subset if r["turns_to_completion"]]
        lines.append(
            f"\n**{lang.upper()}**: {sum(r['passed'] for r in subset)}/{len(subset)} passed, "
            f"median turns {statistics.median(turns) if turns else '-'}, "
            f"median LLM {int(statistics.median(llm_all)) if llm_all else '-'}ms, "
            f"median tool {int(statistics.median(tool_all)) if tool_all else '-'}ms")
    lines.append("\nASR/TTS/network latency are not measurable offline — "
                 "pull them per-call from the Vapi dashboard logs on the live number.")
    return "\n".join(lines)


async def main() -> None:
    filt = sys.argv[1] if len(sys.argv) > 1 else ""
    llm = get_provider()
    results = []
    for make in ALL_SCENARIOS:
        scenario = make()
        if filt and filt not in scenario.name:
            continue
        print(f"running {scenario.name}…", flush=True)
        results.append(await run_scenario(llm, lambda s=scenario: s))
    out = Path(__file__).parent / "results.json"
    if filt and out.exists():  # partial rerun: merge over previous results
        previous = {r["scenario"]: r for r in json.loads(out.read_text())}
        previous.update({r["scenario"]: r for r in results})
        results = list(previous.values())
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print(summarize(results))
    print(f"\nfull transcripts: {out}")
    if not all(r["passed"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
