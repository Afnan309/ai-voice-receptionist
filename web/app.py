"""Receptionist dashboard, call simulator and Vapi webhook.

Run:  python web/app.py  ->  http://localhost:8300
"""
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from receptionist import vapi  # noqa: E402
from receptionist.calendar import BUSINESS, Calendar, window_label  # noqa: E402
from receptionist.dialog import Receptionist, summarize  # noqa: E402
from receptionist.store import CallStore  # noqa: E402

app = Flask(__name__, template_folder="templates")
cal = Calendar(ROOT / "data" / "schedule.db")
store = CallStore(ROOT / "data" / "calls.db")
agent = Receptionist(cal)
SESSIONS = {}
OUTCOME_LABEL = {"booked": "Booked", "emergency_booked": "Urgent · booked", "escalated": "Emergency · escalated",
                 "faq_answered": "Question answered", "transferred": "Transferred", "out_of_area": "Out of area",
                 "not_served": "Not a service we offer", "cancelled": "Cancelled", "rescheduled": "Rescheduled",
                 "incomplete": "Incomplete"}


def sms_for(booking_id):
    path = ROOT / "data" / "sms_outbox.jsonl"
    if not booking_id or not path.exists():
        return None
    for line in path.read_text().splitlines():
        rec = json.loads(line)
        if rec["booking_id"] == booking_id:
            return rec
    return None


@app.get("/")
def dashboard():
    calls = store.all()
    counts = Counter(c["outcome"] for c in calls)
    return render_template("dashboard.html", calls=calls, labels=OUTCOME_LABEL, biz=BUSINESS,
                           kpi={"calls": len(calls), "booked": counts["booked"] + counts["emergency_booked"],
                                "emerg": counts["escalated"] + counts["emergency_booked"],
                                "after": sum(1 for c in calls if not (8 <= int(c["started"][11:13]) < 18))})


@app.get("/call/<call_id>")
def call_view(call_id):
    c = store.get(call_id)
    return render_template("call.html", c=c, labels=OUTCOME_LABEL, sms=sms_for(c["booking_id"]), biz=BUSINESS)


@app.get("/schedule")
def schedule():
    start = date.fromisoformat(request.args.get("week", "2026-11-09"))
    days = [start + timedelta(days=i) for i in range(6)]
    grid = {}
    for b in cal.all():
        if b["status"] == "booked":
            grid.setdefault((b["technician"], b["day"], b["start_hour"]), b)
    return render_template("schedule.html", days=days, techs=[t["name"] for t in BUSINESS["technicians"]],
                           windows=BUSINESS["windows"], grid=grid, window_label=window_label, biz=BUSINESS)


@app.get("/simulator")
def simulator():
    return render_template("simulator.html", biz=BUSINESS)


@app.post("/api/sim/start")
def sim_start():
    now = datetime.fromisoformat(request.json.get("now")) if request.json and request.json.get("now") else datetime.now()
    call = agent.start(now)
    sid = str(len(SESSIONS) + 1)
    SESSIONS[sid] = call
    return jsonify({"session": sid, "reply": call.transcript[-1]["text"]})


@app.post("/api/sim/say")
def sim_say():
    call = SESSIONS[request.json["session"]]
    reply = agent.hear(call, request.json["text"])
    done = call.stage == "ended" or call.outcome not in ("incomplete",) and call.stage == "wrap_up"
    if call.stage == "ended":
        store.save(started=call.now.isoformat(timespec="seconds"), channel="simulator", caller=call.slots.get("name"),
                   phone=call.slots.get("phone"), outcome=call.outcome,
                   emergency=call.emergency["reason"] if call.emergency else None,
                   booking_id=call.booking["booking_id"] if call.booking else None, summary=summarize(call),
                   transcript=call.transcript, events=call.events, slots=call.slots)
    sl = call.slots
    pretty = {"service": BUSINESS["services"][sl["service"]]["label"] if sl.get("service") else None,
              "address": " ".join(x for x in (sl.get("street"), sl.get("zip")) if x) or None,
              "name": sl.get("name"), "phone": sl.get("phone"),
              "day": sl["day"].strftime("%A %b ") + str(sl["day"].day) if sl.get("day") else None,
              "time": {(8, 12): "morning", (12, 16): "afternoon", (16, 18): "evening"}.get(tuple(sl["hours"]), f"from {sl['hours'][0]}:00")
              if sl.get("hours") else None}
    return jsonify({"reply": reply, "ended": call.stage == "ended", "outcome": call.outcome, "events": call.events,
                    "slots": {k: v for k, v in pretty.items() if v}, "done": done})


@app.post("/vapi/webhook")
def vapi_webhook():
    return jsonify(vapi.handle(request.get_json(force=True), cal, store))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8300)
