"""Simulated agent: the same system prompt + tool schema the live Vapi
assistant uses, driven by our LLM provider against the real tools and a real
DB. What passes here is the same logic the phone agent runs — only ASR/TTS
and telephony transport differ (measured separately on the live number)."""
import json
import time as time_mod

from packages.conversation import agent_tools
from packages.conversation.tool_schema import TOOL_SCHEMAS
from packages.llm.provider import LLMProvider
from prompts import load_prompt

MAX_TOOL_ROUNDS = 6


class SimulatedAgent:
    def __init__(self, llm: LLMProvider, db, caller_phone: str) -> None:
        self.llm = llm
        self.db = db
        self.phone = caller_phone
        self.messages: list[dict] = [
            {"role": "system", "content": load_prompt("agent_system")}
        ]
        self.tool_log: list[dict] = []  # {name, ms, ok}
        self.llm_ms: list[int] = []

    async def turn(self, user_text: str) -> str:
        """One caller turn -> assistant reply, executing any tool calls."""
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(MAX_TOOL_ROUNDS):
            resp = await self.llm.chat(self.messages, tools=TOOL_SCHEMAS)
            self.llm_ms.append(resp["latency_ms"])
            if not resp["tool_calls"]:
                reply = resp["content"] or ""
                self.messages.append({"role": "assistant", "content": reply})
                return reply
            self.messages.append({
                "role": "assistant", "content": resp["content"],
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"],
                                  "arguments": json.dumps(tc["arguments"])}}
                    for tc in resp["tool_calls"]
                ],
            })
            for tc in resp["tool_calls"]:
                result = await self._execute(tc["name"], tc["arguments"])
                self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                      "content": json.dumps(result)})
        return "(agent exceeded tool budget)"

    async def _execute(self, name: str, args: dict) -> dict:
        handler = agent_tools.REGISTRY.get(name)
        started = time_mod.monotonic()
        if handler is None:
            result: dict = {"ok": False, "error": f"unknown tool {name}"}
        else:
            if "phone" in handler.__code__.co_varnames:
                args["phone"] = self.phone
            args.pop("call_sid", None)
            try:
                result = await handler(self.db, **args)
            except TypeError as exc:
                result = {"ok": False, "error": f"bad arguments: {exc}"}
            except Exception as exc:  # tools must never kill a call
                await self.db.rollback()
                result = {"ok": False, "error": f"internal: {exc}"}
        self.tool_log.append({"name": name,
                              "ms": int((time_mod.monotonic() - started) * 1000),
                              "ok": bool(result.get("ok"))})
        return result

    def transcript(self) -> list[dict]:
        return [m for m in self.messages
                if m["role"] in ("user", "assistant") and m.get("content")]
