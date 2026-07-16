"""Create/update the Vapi assistant with our prompt, tools and voice settings.

Usage:
    VAPI_API_KEY=... PUBLIC_URL=https://your-backend VAPI_SECRET=... \
        python scripts/vapi_setup.py

Prints the assistant id. Attach a phone number to it in the Vapi dashboard
(or via their /phone-number API) and the agent is live.
"""
import asyncio
import json
import sys

import httpx

from packages.conversation.tool_schema import TOOL_SCHEMAS
from packages.shared.config import get_settings
from prompts import load_prompt

VAPI_API = "https://api.vapi.ai"
ASSISTANT_NAME = "city-dental-receptionist"


def build_assistant_payload(s) -> dict:
    webhook = f"{s.public_url}/api/vapi/webhook"
    tools = [
        {
            "type": "function",
            "async": False,
            "function": schema,
            "server": {"url": webhook, "secret": s.vapi_secret},
        }
        for schema in TOOL_SCHEMAS
    ]
    return {
        "name": ASSISTANT_NAME,
        "model": {
            "provider": "openai",
            "model": "gpt-4o",  # strong multilingual tool-calling; see README latency notes
            "temperature": 0.3,
            "messages": [{"role": "system", "content": load_prompt("agent_system")}],
            "tools": tools,
        },
        # multilingual ASR: Deepgram nova-2 with language detection covers EN/HI code-switching
        "transcriber": {"provider": "deepgram", "model": "nova-2", "language": "multi"},
        # multilingual, natural TTS voice (ElevenLabs turbo keeps latency low)
        "voice": {"provider": "11labs", "voiceId": "cgSgspJ2msm6clMCkdW9",
                  "model": "eleven_turbo_v2_5"},
        "firstMessage": "Thank you for calling City Dental Care, this is Maya. How can I help?",
        "firstMessageMode": "assistant-speaks-first",
        "silenceTimeoutSeconds": 20,
        "maxDurationSeconds": 900,
        "backgroundSound": "off",
        "server": {"url": webhook, "secret": s.vapi_secret},
        "serverMessages": ["tool-calls", "status-update", "end-of-call-report"],
        "startSpeakingPlan": {"waitSeconds": 0.4},  # snappy turn-taking
        "stopSpeakingPlan": {"numWords": 2},  # barge-in after 2 caller words
    }


async def main() -> None:
    s = get_settings()
    if not s.vapi_api_key:
        sys.exit("Set VAPI_API_KEY (and PUBLIC_URL + VAPI_SECRET) in the environment/.env")
    headers = {"Authorization": f"Bearer {s.vapi_api_key}"}
    payload = build_assistant_payload(s)
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        existing = [
            a for a in (await client.get(f"{VAPI_API}/assistant")).json()
            if a.get("name") == ASSISTANT_NAME
        ]
        if existing:
            resp = await client.patch(f"{VAPI_API}/assistant/{existing[0]['id']}", json=payload)
        else:
            resp = await client.post(f"{VAPI_API}/assistant", json=payload)
        resp.raise_for_status()
        assistant = resp.json()
        print(json.dumps({"assistant_id": assistant["id"],
                          "updated": bool(existing)}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
