"""Vapi server-URL handler.

Vapi runs the phone call (speech-to-text, LLM, text-to-speech) and POSTs "server messages" to our webhook.
We handle:
  * tool-calls          -> run our business tools and return results
  * end-of-call-report  -> store transcript and summary in the call log
Payload shapes follow Vapi's server-message format; check them against the current Vapi docs when connecting.
"""
from __future__ import annotations

import json
from datetime import date, datetime

from . import nlu
from .calendar import Calendar
from .store import CallStore
from .tools import (alert_on_call, assess_emergency, book_appointment, cancel_appointment, check_availability,
                    service_info)


def _args(raw) -> dict:
    return json.loads(raw) if isinstance(raw, str) else (raw or {})


def run_tool(cal: Calendar, name: str, a: dict, now: datetime) -> dict:
    if name == "check_availability":
        day = nlu.extract_day(a.get("preferred_day", ""), now.date()) if a.get("preferred_day") else None
        if a.get("preferred_day") and not day:
            try:
                day = date.fromisoformat(a["preferred_day"])
            except ValueError:
                day = None
        hours = nlu.extract_hours(a.get("time_preference", "")) if a.get("time_preference") else None
        slots = check_availability(cal, a["service"], now, day, hours)
        return {"slots": slots, "say": " or ".join(s["label"] for s in slots) or "no openings in the next two weeks"}
    if name == "book_appointment":
        slot = {"day": a["day"], "start_hour": int(a["start_hour"]), "technician": a.get("technician", ""),
                "label": a.get("label", "")}
        booking = book_appointment(cal, slot, a["service"], a["name"], a["phone"],
                                   f"{a.get('address', '')} {a.get('zip', '')}".strip(), bool(a.get("emergency")))
        return {"booking_id": booking["booking_id"], "technician": booking["technician"], "sms_sent": True}
    if name == "assess_emergency":
        return assess_emergency(a.get("description", "")) or {"level": "none"}
    if name == "alert_on_call":
        return alert_on_call(a.get("reason", "emergency"), a.get("phone"), a.get("address"))
    if name == "get_service_info":
        return {"answer": service_info(a.get("service"))}
    if name == "check_service_area":
        return {"in_area": nlu.in_service_area(a.get("zip"))}
    if name == "cancel_appointment":
        b = cancel_appointment(cal, a.get("phone", ""))
        return {"cancelled": bool(b), "booking_id": b["id"] if b else None}
    return {"error": f"unknown tool {name}"}


def handle(payload: dict, cal: Calendar, store: CallStore, now: datetime | None = None) -> dict:
    now = now or datetime.now()
    msg = payload.get("message", {})
    kind = msg.get("type")
    if kind == "tool-calls":
        results = []
        for tc in msg.get("toolCallList") or msg.get("toolCalls") or []:
            fn = tc.get("function", {})
            try:
                result = run_tool(cal, fn.get("name"), _args(fn.get("arguments")), now)
            except Exception as exc:          # tell the LLM what went wrong so it can recover in the call
                result = {"error": str(exc)}
            results.append({"toolCallId": tc.get("id"), "result": json.dumps(result)})
        return {"results": results}
    if kind == "end-of-call-report":
        call = msg.get("call", {})
        artifact = msg.get("artifact", {})
        transcript = [{"role": "agent" if m.get("role") in ("assistant", "bot") else "caller", "text": m.get("message", "")}
                      for m in artifact.get("messages", []) if m.get("role") in ("assistant", "bot", "user")]
        customer = call.get("customer", {})
        store.save(call_id=call.get("id"), started=call.get("startedAt", now.isoformat(timespec="seconds")),
                   channel="vapi phone", caller=customer.get("name"), phone=customer.get("number"),
                   outcome=msg.get("endedReason", "completed"), emergency=None, booking_id=None,
                   summary=(msg.get("analysis") or {}).get("summary", ""), transcript=transcript, events=[], slots={})
        return {"ok": True}
    return {"ok": True, "ignored": kind}
