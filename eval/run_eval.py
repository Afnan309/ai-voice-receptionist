"""Run the held-out scripted calls and score the receptionist.

Usage: python eval/run_eval.py [test|dev]
Writes: eval/results_<set>.json
"""
import json
import statistics
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from receptionist.calendar import Calendar, seed_existing  # noqa: E402
from receptionist.dialog import Receptionist  # noqa: E402

DEFAULT_NOW = datetime(2026, 11, 9, 10, 5)


def main():
    split = sys.argv[1] if len(sys.argv) > 1 else "test"
    calls = json.loads((ROOT / f"eval/{split}_calls.json").read_text())["calls"]
    with tempfile.TemporaryDirectory() as tmp:
        cal = Calendar(Path(tmp) / "eval.db")
        seed_existing(cal, DEFAULT_NOW.date())
        agent = Receptionist(cal)
        field_hits, field_total = Counter(), Counter()
        successes, latencies, turns, failures = 0, [], [], []
        emerg = {"life_safety": [0, 0], "urgent": [0, 0]}
        for i, case in enumerate(calls):
            now = datetime.fromisoformat(case["now"]) if case.get("now") else DEFAULT_NOW
            call = agent.start(now)
            for t in case["turns"]:
                t0 = time.perf_counter()
                agent.hear(call, t)
                latencies.append((time.perf_counter() - t0) * 1000)
            turns.append(len(case["turns"]))
            exp, ok = case["expect"], True
            got = {
                "outcome": call.outcome,
                "service": call.slots.get("service"),
                "day": call.booking["day"] if call.booking else None,
                "name": call.slots.get("name"),
                "phone": call.slots.get("phone"),
                "emergency_level": call.emergency["level"] if call.emergency else None,
            }
            for k, v in exp.items():
                field_total[k] += 1
                if got[k] == v:
                    field_hits[k] += 1
                else:
                    ok = False
            if exp.get("emergency_level"):
                emerg[exp["emergency_level"]][1] += 1
                emerg[exp["emergency_level"]][0] += got["emergency_level"] == exp["emergency_level"]
            successes += ok
            if not ok:
                failures.append({"call": i, "expected": exp, "got": {k: got[k] for k in exp}})

        rows = cal.all()
        booked = [r for r in rows if r["status"] == "booked"]
        double = len(booked) - len({(r["day"], r["start_hour"], r["technician"]) for r in booked})
        ai_bookings = [r for r in booked if r["source"] == "ai-receptionist"]

    results = {
        "test_calls": len(calls),
        "task_success": successes, "task_success_rate": round(successes / len(calls), 3),
        "field_accuracy": {k: f"{field_hits[k]}/{field_total[k]}" for k in field_total},
        "life_safety_escalated": f"{emerg['life_safety'][0]}/{emerg['life_safety'][1]}",
        "urgent_detected": f"{emerg['urgent'][0]}/{emerg['urgent'][1]}",
        "bookings_made": len(ai_bookings), "double_bookings": double,
        "avg_caller_turns": round(statistics.mean(turns), 1),
        "avg_response_ms": round(statistics.mean(latencies), 2),
        "p95_response_ms": round(sorted(latencies)[int(0.95 * len(latencies)) - 1], 2),
        "failures": failures,
    }
    results["split"] = split
    (ROOT / f"eval/results_{split}.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
