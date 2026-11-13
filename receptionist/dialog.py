"""Dialog manager: runs one phone conversation turn by turn.

Goal-driven slot filling: understand the need -> safety triage -> collect service, address, name, phone and
preferred time -> offer real open windows -> book -> text a confirmation -> summarise the call.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from . import nlu
from .calendar import BUSINESS, Calendar, day_label, window_label
from .tools import (alert_on_call, assess_emergency, book_appointment, cancel_appointment, check_availability,
                    is_open, service_info)

GREETING = f"Thanks for calling {BUSINESS['name']}, this is Ava, the virtual assistant. How can I help you today?"


@dataclass
class Call:
    now: datetime
    transcript: list[dict] = field(default_factory=list)
    slots: dict = field(default_factory=dict)
    stage: str = "start"                 # start | booking | offer | cancel | escalate | wrap_up | ended
    asked: str | None = None             # which detail we just asked for
    offered: list[dict] = field(default_factory=list)
    outcome: str = "incomplete"
    booking: dict | None = None
    emergency: dict | None = None
    events: list[str] = field(default_factory=list)


class Receptionist:
    def __init__(self, calendar: Calendar):
        self.cal = calendar

    # ------------------------------------------------------------------ public
    def start(self, now: datetime) -> Call:
        call = Call(now=now)
        self._say(call, GREETING)
        return call

    def hear(self, call: Call, text: str) -> str:
        call.transcript.append({"role": "caller", "text": text})
        if call.stage == "ended":
            return ""
        reply = self._respond(call, text)
        self._say(call, reply)
        return reply

    # ------------------------------------------------------------------ internals
    def _say(self, call: Call, text: str) -> None:
        call.transcript.append({"role": "agent", "text": text})

    def _absorb(self, call: Call, text: str) -> None:
        s = call.slots
        today = call.now.date()
        if not s.get("service") and (svc := nlu.extract_service(text)):
            s["service"] = svc
        if (day := nlu.extract_day(text, today)):
            s["day"] = day
        if (hours := nlu.extract_hours(text)):
            s["hours"] = hours
        if re.search(r"\b(earliest|soonest|first available|whenever|any ?time|as soon as)\b", text.lower()):
            s["flexible"] = True
        if (phone := nlu.extract_phone(text)):
            s["phone"] = phone
        if (addr := nlu.extract_address(text)):
            if addr["street"]:
                s["street"] = addr["street"]
            if addr["zip"]:
                s["zip"] = addr["zip"]
        name = nlu.extract_name(text)
        if not name and call.asked == "name":
            words = re.findall(r"[A-Za-z'\-]+", text)
            if 1 <= len(words) <= 3 and nlu.yes_no(text) is None and not {w.lower() for w in words} & nlu.NOT_NAME:
                name = " ".join(w if any(c.isupper() for c in w) else w.capitalize() for w in words)
        if name:
            s["name"] = name

    def _respond(self, call: Call, text: str) -> str:
        self._absorb(call, text)
        s = call.slots

        # 1. safety first, on any turn
        if not call.emergency and (tri := assess_emergency(text)):
            call.emergency = tri
            call.events.append(f"triage: {tri['level']} ({tri['reason']})")
            if tri["level"] == "life_safety":
                call.stage = "escalate"
                return tri["instructions"] + " What's the best number for the technician to call you back on?"
            water = tri["reason"] == "active water leak"
            s["service"] = "plumbing_repair" if water else (nlu.extract_service(text) or "hvac_repair")
            s["flexible"] = True
            s["day"] = call.now.date()
            call.stage = "booking"
            return tri["instructions"] + " " + self._next_question(call)

        if call.stage == "escalate":
            if not s.get("phone"):
                return "Sorry, I didn't catch that. What number should the technician call?"
            if not s.get("street"):
                call.asked = "address"
                return "Got it. And what's the address there?"
            alert_on_call(call.emergency["reason"], s.get("phone"), s.get("street"))
            call.events.append("on-call technician alerted")
            call.outcome, call.stage = "escalated", "ended"
            return (f"Thank you. I've sent an urgent alert to our on-call technician with your number {s['phone']}, "
                    "and they'll call you within a few minutes. Please stay outside and safe.")

        if call.stage == "start":
            label, conf = nlu.intent(text)
            call.events.append(f"intent: {label} ({conf:.0%})")
            if s.get("service") and label not in ("pricing_question", "hours_question", "cancel", "reschedule", "talk_to_human"):
                label = "book_service"
            if label == "pricing_question":
                call.slots["faq"] = "pricing"
                call.stage = "offer_booking"
                return service_info(s.get("service")) + " Would you like me to book a visit?"
            if label == "hours_question":
                call.slots["faq"] = "business hours"
                call.stage = "offer_booking"
                return f"We're open {BUSINESS['hours']} Is there something I can book for you?"
            if label in ("cancel", "reschedule"):
                call.stage = label
                call.asked = "phone"
                return "No problem. What's the phone number on the appointment?" if not s.get("phone") else self._respond(call, "")
            if label == "talk_to_human":
                call.outcome, call.stage = "transferred", "ended"
                if is_open(call.now):
                    return "Of course, I'll transfer you to our office team now. One moment please."
                return "Our office is closed right now, so I'll take a message and someone will call you first thing tomorrow."
            if label == "other" and not s.get("service"):
                call.outcome, call.stage = "not_served", "ended"
                return ("I'm sorry, we only handle heating, cooling, plumbing and electrical work for homes. "
                        "Thanks for calling, and have a great day.")
            call.stage = "booking"
            return self._next_question(call)

        if call.stage == "offer_booking":
            if nlu.yes_no(text) is False:
                call.outcome, call.stage = "faq_answered", "ended"
                return "No problem. Thanks for calling, and have a great day."
            call.stage = "booking"
            return self._next_question(call)

        if call.stage in ("cancel", "reschedule"):
            if not s.get("phone"):
                return "Sorry, could you repeat the phone number on the appointment?"
            booking = cancel_appointment(self.cal, s["phone"])
            if not booking:
                call.outcome, call.stage = "not_found", "ended"
                return "I couldn't find an appointment under that number. I'll have the office call you back to sort it out."
            when = f"{day_label(date.fromisoformat(booking['day']), call.now.date())} between {window_label(booking['start_hour'])}"
            call.events.append(f"cancelled {booking['id']}")
            if call.stage == "cancel":
                call.outcome, call.stage = "cancelled", "ended"
                return f"Done. I've cancelled your appointment for {when}. Is there anything else? Otherwise, have a great day."
            s.update(service=booking["service"], name=booking["customer"], street=booking["address"], zip="80202", rescheduled=True)
            call.stage = "booking"
            call.asked = "time"
            return f"I've found your visit for {when}. What day and time would work better?"

        if call.stage == "booking":
            return self._next_question(call)

        if call.stage == "offer":
            choice = nlu.pick_slot(text, call.offered)
            if choice is None and nlu.yes_no(text) is True and len(call.offered) == 1:
                choice = 0
            if choice is None:
                if nlu.yes_no(text) is False or s.get("day") or s.get("hours"):
                    call.offered = []
                    return self._offer(call, excluded=True)
                return "Sorry, which of those times works better for you, the first or the second?"
            return self._book(call, call.offered[choice])

        if call.stage == "wrap_up":
            call.stage = "ended"
            if nlu.yes_no(text) is True and len(text.split()) > 3:
                return "Our office will follow up on that. Thanks for calling Summit, and have a great day."
            return f"Thanks for calling {BUSINESS['name']}. Have a great day!"
        return "Sorry, could you say that again?"

    def _next_question(self, call: Call) -> str:
        s = call.slots
        if not s.get("service"):
            call.asked = "service"
            return "Sure. What's going on, is it heating, cooling, plumbing or electrical?"
        if not s.get("zip") and not s.get("street"):
            call.asked = "address"
            lead = "" if call.emergency else "I can help with that. "
            return lead + "What's the service address, including the zip code?"
        if not s.get("zip"):
            call.asked = "zip"
            return "And what's the zip code there?"
        if nlu.in_service_area(s["zip"]) is False:
            call.outcome, call.stage = "out_of_area", "ended"
            return (f"I'm sorry, {s['zip']} is outside our service area, so we can't send a technician there. "
                    "Thanks for calling.")
        if not s.get("name"):
            call.asked = "name"
            return "Thanks. Can I get your first and last name?"
        if not s.get("phone"):
            call.asked = "phone"
            return f"Thanks, {s['name'].split()[0]}. What's the best phone number to text your confirmation to?"
        if not (s.get("day") or s.get("hours") or s.get("flexible")):
            call.asked = "time"
            return "Perfect. What day and time works best for you?"
        return self._offer(call)

    def _offer(self, call: Call, excluded: bool = False) -> str:
        s = call.slots
        slots = check_availability(self.cal, s["service"], call.now, s.get("day"), s.get("hours"), limit=3 if excluded else 2)
        if excluded and call.offered:
            slots = [x for x in slots if x not in call.offered]
        slots = slots[:2]
        if not slots:
            call.outcome, call.stage = "no_availability", "ended"
            return "I'm sorry, I don't see any openings in the next two weeks. I'll have the office call you to find a time."
        call.offered, call.stage, call.asked = slots, "offer", "slot"
        prefix = "The earliest I can get a technician out is" if call.emergency else "I have"
        if len(slots) == 1:
            return f"{prefix} {slots[0]['label']}. Does that work?"
        return f"{prefix} {slots[0]['label']}, or {slots[1]['label']}. Which works better?"

    def _book(self, call: Call, slot: dict) -> str:
        s = call.slots
        address = f"{s.get('street') or ''} {s.get('zip') or ''}".strip()
        call.booking = book_appointment(self.cal, slot, s["service"], s["name"], s["phone"], address,
                                        emergency=bool(call.emergency))
        call.events.append(f"booked {call.booking['booking_id']} with {call.booking['technician']}")
        call.events.append("SMS confirmation sent")
        call.outcome = "rescheduled" if s.get("rescheduled") else ("emergency_booked" if call.emergency else "booked")
        call.stage = "wrap_up"
        fee = f" Since this is an urgent visit, there's a {BUSINESS['emergency_fee']}." if call.emergency and not is_open(call.now) else ""
        return (f"You're all set. {call.booking['technician']} will arrive {slot['label']}.{fee} "
                f"I've just texted a confirmation to {s['phone']}. Is there anything else I can help with?")


def summarize(call: Call) -> str:
    s = call.slots
    who = s.get("name", "Caller")
    parts = [f"{who} called"]
    if call.emergency:
        r = call.emergency["reason"]
        parts.append(f"about {'an' if r[0] in 'aeiou' else 'a'} {r} ({call.emergency['level'].replace('_', ' ')})")
    elif s.get("service"):
        parts.append(f"about {BUSINESS['services'][s['service']]['label']}")
    text = " ".join(parts) + "."
    if call.booking:
        text += (f" Booked {call.booking['booking_id']} with {call.booking['technician']} for "
                 f"{call.booking['label']}; SMS confirmation sent to {s.get('phone')}.")
    elif call.outcome == "escalated":
        text += f" On-call technician alerted to call back {s.get('phone')}."
    elif call.outcome == "out_of_area":
        text += f" Zip {s.get('zip')} is outside the service area."
    elif call.outcome == "faq_answered":
        text = f"{who} asked about {s.get('faq', 'our services')}. Question answered, no booking needed."
    elif call.outcome == "cancelled":
        text += " Appointment cancelled."
    elif call.outcome == "transferred":
        text = f"{who} asked to speak with a person. Transferred to the office."
    elif call.outcome == "not_served":
        text = f"{who} asked about a service we don't offer. Politely declined."
    return text
