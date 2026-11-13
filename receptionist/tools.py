"""Business tools. The same functions back the Vapi tool-call webhook (production voice calls)
and the local dialog manager (simulator, evaluation)."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .calendar import BUSINESS, Calendar, window_label
from .triage import triage

ROOT = Path(__file__).resolve().parents[1]
SMS_LOG = ROOT / "data" / "sms_outbox.jsonl"
ALERTS = ROOT / "data" / "oncall_alerts.jsonl"


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def is_open(now: datetime) -> bool:
    return now.weekday() in BUSINESS["workdays"] and 8 <= now.hour < 18


def service_info(service: str | None) -> str:
    if service and service in BUSINESS["services"]:
        s = BUSINESS["services"][service]
        return f"For {s['label']}, it's {s['price']}."
    return ("A standard visit starts with an $89 diagnostic, waived if you go ahead with the repair. "
            "AC tune-ups are $129 and furnace tune-ups are $119.")


def check_availability(cal: Calendar, service: str, now: datetime, day: date | None = None,
                       hours: tuple[int, int] | None = None, limit: int = 2) -> list[dict]:
    # relax preferences step by step instead of dead-ending: time first, then day
    for d, h in ((day, hours), (day, None), (None, hours), (None, None)):
        slots = cal.find_slots(service, now, d, h, limit)
        if slots:
            return slots
    return []


def book_appointment(cal: Calendar, slot: dict, service: str, name: str, phone: str, address: str,
                     emergency: bool = False) -> dict:
    booking = cal.book(slot, service, name, phone, address, emergency)
    label = BUSINESS["services"][service]["label"]
    day = date.fromisoformat(slot["day"]).strftime("%a %b ") + str(date.fromisoformat(slot["day"]).day)
    sms = (f"{BUSINESS['name']}: Hi {name.split()[0]}, your {label} is booked for {day}, "
           f"{window_label(slot['start_hour'])}. Tech: {booking['technician']}. "
           f"Ref {booking['booking_id']}. Reply R to reschedule.")
    _append(SMS_LOG, {"to": phone, "body": sms, "booking_id": booking["booking_id"], "sent_at": datetime.now().isoformat(timespec="seconds")})
    return {**booking, "sms": sms}


def alert_on_call(reason: str, phone: str | None, address: str | None) -> dict:
    alert = {"reason": reason, "callback": phone, "address": address, "at": datetime.now().isoformat(timespec="seconds")}
    _append(ALERTS, alert)
    return alert


def assess_emergency(text: str) -> dict | None:
    return triage(text)


def cancel_appointment(cal: Calendar, phone: str) -> dict | None:
    booking = cal.find_by_phone(phone)
    if booking:
        cal.cancel(booking["id"])
    return booking
