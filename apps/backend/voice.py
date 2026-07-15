"""Twilio voice webhook — the phone entry point."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.conversation.engine import ConversationEngine, Reply
from packages.database.models import CallSession
from packages.database.session import get_db
from packages.llm.provider import get_provider
from packages.shared.config import get_settings
from packages.telephony import twilio

logger = logging.getLogger("voice")
router = APIRouter(prefix="/api/voice", tags=["voice"])
_engine: ConversationEngine | None = None


def engine() -> ConversationEngine:
    global _engine
    if _engine is None:
        _engine = ConversationEngine(get_provider())
    return _engine


@router.post("")
async def voice_webhook(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    form = dict(await request.form())
    signature = request.headers.get("X-Twilio-Signature", "")
    if not twilio.validate_signature(get_settings().twilio_auth_token,
                                     str(request.url), form, signature):
        raise HTTPException(403, "Bad Twilio signature")

    sid = str(form.get("CallSid", ""))
    if not sid:
        raise HTTPException(400, "Missing CallSid")
    text = str(form.get("SpeechResult", "") or "")

    call = (await db.execute(
        select(CallSession).where(CallSession.sid == sid))).scalar_one_or_none()
    if call is None:
        call = CallSession(sid=sid, caller=str(form.get("From", "unknown")))
        db.add(call)
        await db.flush()

    reply: Reply = await engine().handle(db, call, text)

    action_url = str(request.url_for("voice_webhook"))
    if reply.action == "hangup":
        xml = twilio.hangup_response(reply.say, call.language)
    elif reply.action == "transfer":
        xml = twilio.transfer_response(reply.say, get_settings().transfer_number, call.language)
    else:
        xml = twilio.gather_response(reply.say, action_url, call.language)
    return Response(content=xml, media_type="application/xml")
