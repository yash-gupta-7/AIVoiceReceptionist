"""Vapi server-URL webhook: tool calls + call lifecycle events.

Vapi POSTs {"message": {...}} for every event. We handle:
- "tool-calls": dispatch to packages.conversation.agent_tools and reply
  {"results": [{"toolCallId", "result"}]}
- "status-update" / "end-of-call-report": persist the call session so dropped
  calls can be resumed and transcripts show in the dashboard.
Auth: shared secret in the x-vapi-secret header (set in the Vapi dashboard).
"""
import json
import logging
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.conversation import agent_tools
from packages.database.models import CallSession, Message, PMSRecord
from packages.database.session import get_db
from packages.shared import clock
from packages.shared.config import get_settings
from packages.shared.logging import log

logger = logging.getLogger("vapi")
router = APIRouter(prefix="/api/vapi", tags=["vapi"])
pms_router = APIRouter(prefix="/api/mock-pms", tags=["mock-pms"])


def _check_secret(x_vapi_secret: str | None = Header(default=None)) -> None:
    expected = get_settings().vapi_secret
    if expected and not secrets.compare_digest(x_vapi_secret or "", expected):
        raise HTTPException(403, "bad vapi secret")


async def _get_call(db: AsyncSession, message: dict) -> CallSession | None:
    call = message.get("call") or {}
    sid = call.get("id", "")
    if not sid:
        return None
    session = (
        await db.execute(select(CallSession).where(CallSession.sid == sid))
    ).scalar_one_or_none()
    if session is None:
        caller = (call.get("customer") or {}).get("number", "unknown")
        session = CallSession(sid=sid, caller=caller, state="vapi")
        db.add(session)
        await db.flush()
    return session


@router.post("/webhook", dependencies=[Depends(_check_secret)])
async def vapi_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    body = await request.json()
    message = body.get("message", {})
    mtype = message.get("type", "")
    session = await _get_call(db, message)

    if mtype == "tool-calls":
        results = []
        for tc in message.get("toolCallList", []):
            name = (tc.get("function") or {}).get("name") or tc.get("name", "")
            raw_args = (tc.get("function") or {}).get("arguments") or tc.get("arguments") or {}
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            handler = agent_tools.REGISTRY.get(name)
            if handler is None:
                result: dict = {"ok": False, "error": f"unknown tool {name}"}
            else:
                # inject caller number for phone-keyed tools if the LLM omitted it
                if "phone" in handler.__code__.co_varnames and not args.get("phone") and session:
                    args["phone"] = session.caller
                if name == "book_appointment" and session:
                    args["call_sid"] = session.sid
                try:
                    result = await handler(db, **args)
                except TypeError as exc:
                    result = {"ok": False, "error": f"bad arguments: {exc}"}
                except Exception:
                    logger.exception("tool %s failed", name)
                    await db.rollback()
                    result = {"ok": False,
                              "error": "temporary system issue, apologise and retry once"}
            log(logger, "vapi_tool", tool=name, ok=result.get("ok"))
            results.append({"toolCallId": tc.get("id"), "result": json.dumps(result)})
        await db.commit()
        return {"results": results}

    if mtype == "status-update" and session:
        status = message.get("status", "")
        if status == "ended" and session.outcome is None:
            # keep outcome NULL only for calls that ended without resolution
            ended_reason = message.get("endedReason", "")
            session.ended_at = clock.now()
            if "error" not in ended_reason and "customer" not in ended_reason:
                session.outcome = "completed"
        await db.commit()

    if mtype == "end-of-call-report" and session:
        session.ended_at = session.ended_at or clock.now()
        analysis = message.get("analysis") or {}
        session.data = {**(session.data or {}),
                        "summary": analysis.get("summary", "")}
        for m in (message.get("artifact") or {}).get("messages", []):
            if m.get("role") in ("user", "bot", "assistant") and m.get("message"):
                role = "caller" if m["role"] == "user" else "assistant"
                db.add(Message(call_id=session.id, role=role, text=m["message"]))
        if analysis.get("successEvaluation") in ("true", True):
            session.outcome = session.outcome or "completed"
        await db.commit()

    return {"ok": True}


# ---------------- Mock PMS (the EHR write-back target) ----------------

@pms_router.post("/records", status_code=201)
async def pms_create_record(
    request: Request,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
    fail: int = 0,
) -> dict:
    """Mock EHR/PMS ingest. Idempotent on Idempotency-Key: a replay returns the
    original record with 201 and creates nothing. ?fail=1 (or PMS_FAIL=1 env)
    simulates an outage for failure-path testing."""
    import os
    if fail or os.environ.get("PMS_FAIL") == "1":
        raise HTTPException(503, "PMS unavailable (simulated)")
    if not idempotency_key:
        raise HTTPException(400, "Idempotency-Key header required")
    body = await request.json()
    existing = (
        await db.execute(select(PMSRecord).where(PMSRecord.idempotency_key == idempotency_key))
    ).scalar_one_or_none()
    if existing:
        return {"id": existing.id, "replayed": True}
    record = PMSRecord(idempotency_key=idempotency_key,
                       kind=body.get("kind", "appointment"), payload=body.get("payload", {}))
    db.add(record)
    await db.commit()
    return {"id": record.id, "replayed": False}


@pms_router.get("/records")
async def pms_list_records(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(PMSRecord).order_by(PMSRecord.id.desc()).limit(100))).scalars()
    return [{"id": r.id, "key": r.idempotency_key, "kind": r.kind,
             "payload": r.payload, "created_at": r.created_at.isoformat()} for r in rows]
