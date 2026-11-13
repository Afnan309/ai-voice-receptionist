"""Reset the demo data and replay a day of sample calls through the receptionist.

Usage: python scripts/seed_demo.py
Calls are labelled channel="simulator" in the dashboard; real phone calls arrive through /vapi/webhook.
"""
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from receptionist.calendar import Calendar, seed_existing  # noqa: E402
from receptionist.dialog import Receptionist, summarize  # noqa: E402
from receptionist.store import CallStore  # noqa: E402

DAY = date(2026, 11, 9)
CALLS = [
    ("07:42", ["Hi, my AC is blowing warm air and I'd like someone to come take a look", "It's 1450 Elm Street, 80206",
               "My name is Laura Chen", "Sure, it's 303-555-0142", "Tomorrow afternoon if possible", "The first one works",
               "No, that's everything"]),
    ("09:15", ["Hello, I think I smell gas in my kitchen", "720 555 0199", "It's 88 Pine Court, 80203"]),
    ("10:05", ["How much is a furnace tune up?", "Yes please", "3100 Maple Drive, zip 80014", "Jen Ortiz",
               "(303) 555-0177", "Thursday morning", "the second one", "no that's all"]),
    ("11:30", ["What are your hours?", "No thanks"]),
    ("12:10", ["Hi I need my drain cleaned", "123 Main St 80301"]),
    ("13:45", ["Can I talk to a real person"]),
    ("19:20", ["The pipe under my sink burst and water is everywhere", "1400 Josephine St 80206", "Rosa Diaz",
               "720 555 0126", "first", "no thank you"]),
    ("22:05", ["Our furnace died and the house is freezing", "1500 Pearl Street 80203", "Nina Alvarez", "720 555 0193",
               "first please", "that's all, thanks"]),
]


def main():
    for f in ("schedule.db", "calls.db", "sms_outbox.jsonl", "oncall_alerts.jsonl"):
        (ROOT / "data" / f).unlink(missing_ok=True)
    cal = Calendar(ROOT / "data" / "schedule.db")
    seed_existing(cal, DAY)
    store = CallStore(ROOT / "data" / "calls.db")
    agent = Receptionist(cal)
    for hhmm, turns in CALLS:
        now = datetime.fromisoformat(f"{DAY.isoformat()}T{hhmm}")
        call = agent.start(now)
        for t in turns:
            agent.hear(call, t)
        cid = store.save(started=now.isoformat(timespec="seconds"), channel="simulator", caller=call.slots.get("name"),
                         phone=call.slots.get("phone"), outcome=call.outcome,
                         emergency=call.emergency["reason"] if call.emergency else None,
                         booking_id=call.booking["booking_id"] if call.booking else None, summary=summarize(call),
                         transcript=call.transcript, events=call.events, slots=call.slots)
        print(cid, hhmm, call.outcome)


if __name__ == "__main__":
    main()
