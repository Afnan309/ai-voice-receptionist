import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from receptionist import nlu, vapi  # noqa: E402
from receptionist.calendar import Calendar, seed_existing  # noqa: E402
from receptionist.dialog import Receptionist  # noqa: E402
from receptionist.store import CallStore  # noqa: E402
from receptionist.triage import triage  # noqa: E402

NOW = datetime(2026, 11, 9, 10, 5)      # Monday


@pytest.fixture
def cal(tmp_path):
    c = Calendar(tmp_path / "s.db")
    seed_existing(c, NOW.date())
    return c


@pytest.mark.parametrize("text,expected", [("tomorrow", date(2026, 11, 10)), ("Thursday", date(2026, 11, 12)),
                                           ("next Friday", date(2026, 11, 20)), ("November 20th", date(2026, 11, 20)),
                                           ("11/14", date(2026, 11, 14)), ("whenever", None)])
def test_day_parsing(text, expected):
    assert nlu.extract_day(text, NOW.date()) == expected


@pytest.mark.parametrize("text,expected", [("morning", (8, 12)), ("after 2pm", (14, 18)), ("around 10", (10, 11)),
                                           ("at 3pm", (14, 15))])
def test_time_parsing(text, expected):
    assert nlu.extract_hours(text) == expected


@pytest.mark.parametrize("text,expected", [("303-555-0142", "(303) 555-0142"), ("call me at 1 720 555 0199", "(720) 555-0199"),
                                           ("my zip is 80206", None)])
def test_phone_parsing(text, expected):
    assert nlu.extract_phone(text) == expected


@pytest.mark.parametrize("text,level", [("I smell gas", "life_safety"), ("the CO alarm is beeping", "life_safety"),
                                        ("a pipe burst", "urgent"), ("my AC is noisy", None)])
def test_triage(text, level):
    result = triage(text)
    assert (result["level"] if result else None) == level


def test_no_double_booking(cal):
    slot = cal.find_slots("electrical", NOW, limit=1)[0]          # only Dev does electrical
    cal.book(slot, "electrical", "A", "1", "x")
    with pytest.raises(ValueError):
        cal.book(slot, "electrical", "B", "2", "y")


def run(cal, turns, now=NOW):
    agent = Receptionist(cal)
    call = agent.start(now)
    for t in turns:
        agent.hear(call, t)
    return call


def test_full_booking_sends_sms(cal):
    call = run(cal, ["My AC is blowing warm air", "1450 Elm Street 80206", "Laura Chen", "303 555 0142",
                     "tomorrow afternoon", "first"])
    assert call.outcome == "booked" and call.booking["day"] == "2026-11-10" and "Laura" in call.booking["sms"]


def test_gas_leak_escalates_and_never_books(cal):
    call = run(cal, ["I smell gas", "720 555 0199", "88 Pine Ct 80203"])
    assert call.outcome == "escalated" and call.booking is None


def test_out_of_area_is_declined(cal):
    call = run(cal, ["My drain is clogged", "123 Main St 80301"])
    assert call.outcome == "out_of_area"


def test_preferences_relax_instead_of_dead_end(cal):
    call = run(cal, ["Breaker keeps tripping", "601 Logan St 80203", "Sam Turner", "720 555 0177", "Monday at 8am"])
    assert call.stage == "offer" and call.offered        # Monday 8am is gone, other windows offered


def test_vapi_tool_calls_and_report(cal, tmp_path):
    store = CallStore(tmp_path / "c.db")
    payload = {"message": {"type": "tool-calls", "toolCallList": [
        {"id": "t1", "type": "function", "function": {"name": "check_availability",
                                                      "arguments": {"service": "ac_tuneup", "preferred_day": "Friday"}}},
        {"id": "t2", "type": "function", "function": {"name": "assess_emergency",
                                                      "arguments": json.dumps({"description": "smells like gas"})}}]}}
    out = vapi.handle(payload, cal, store, now=NOW)
    slots = json.loads(out["results"][0]["result"])["slots"]
    assert slots and slots[0]["day"] == "2026-11-13"
    assert json.loads(out["results"][1]["result"])["level"] == "life_safety"
    report = {"message": {"type": "end-of-call-report", "endedReason": "customer-ended-call",
                          "call": {"id": "vapi-1", "customer": {"number": "+13035550100"}},
                          "artifact": {"messages": [{"role": "bot", "message": "Hi"}, {"role": "user", "message": "Hello"}]},
                          "analysis": {"summary": "Caller asked about hours."}}}
    vapi.handle(report, cal, store, now=NOW)
    assert store.get("vapi-1")["summary"] == "Caller asked about hours."
