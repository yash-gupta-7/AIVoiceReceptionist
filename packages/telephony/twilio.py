"""Twilio voice adapter: TwiML generation + webhook signature validation.

ponytail: Twilio <Gather input="speech"> gives us streaming STT, TTS, and
barge-in natively — no custom audio pipeline. Swap this module for a
media-streams implementation (Deepgram/ElevenLabs) when latency or voice
quality demands it. No twilio SDK: TwiML is 10 lines of XML and signature
validation is stdlib hmac.
"""
import base64
import hashlib
import hmac
from xml.sax.saxutils import escape

# ISO language -> Twilio speech recognition / TTS locale
LOCALES = {"en": "en-US", "hi": "hi-IN", "es": "es-ES", "fr": "fr-FR"}


def locale(language: str) -> str:
    return LOCALES.get(language, "en-US")


def validate_signature(auth_token: str, url: str, params: dict, signature: str) -> bool:
    """Twilio request validation: HMAC-SHA1 of url + sorted form params."""
    if not auth_token:  # local dev without Twilio configured
        return True
    payload = url + "".join(k + str(params[k]) for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def gather_response(say: str, action_url: str, language: str = "en") -> str:
    """Speak `say`, then listen for speech. bargeIn lets callers interrupt."""
    loc = locale(language)
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Gather input="speech" action="{escape(action_url)}" method="POST" '
        f'language="{loc}" speechTimeout="auto" bargeIn="true">'
        f'<Say language="{loc}">{escape(say)}</Say></Gather>'
        f'<Redirect method="POST">{escape(action_url)}</Redirect></Response>'
    )


def hangup_response(say: str, language: str = "en") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Say language="{locale(language)}">{escape(say)}</Say><Hangup/></Response>'
    )


def transfer_response(say: str, number: str, language: str = "en") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f'<Say language="{locale(language)}">{escape(say)}</Say>'
        f'<Dial>{escape(number)}</Dial></Response>'
    )
