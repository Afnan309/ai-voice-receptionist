"""Technician calendar: 2-hour arrival windows, skill-based assignment, SQLite bookings."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUSINESS = json.loads((ROOT / "data" / "business.json").read_text())


def window_label(start_hour: int) -> str:
    """'8 and 10 AM', '10 AM and 12 PM', '2 and 4 PM'."""
    end = next(e for s, e in BUSINESS["windows"] if s == start_hour)
    m1, m2 = ("AM" if start_hour < 12 else "PM"), ("AM" if end < 12 else "PM")
    h1, h2 = start_hour % 12 or 12, end % 12 or 12
    return f"{h1} and {h2} {m2}" if m1 == m2 else f"{h1} {m1} and {h2} {m2}"


def day_label(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return d.strftime("%A, %B ") + str(d.day)


class Calendar:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("""CREATE TABLE IF NOT EXISTS bookings(
            id TEXT PRIMARY KEY, day TEXT, start_hour INTEGER, technician TEXT, service TEXT,
            customer TEXT, phone TEXT, address TEXT, emergency INTEGER DEFAULT 0,
            status TEXT DEFAULT 'booked', source TEXT, created_at TEXT)""")
        self.conn.commit()

    # ---------------------------------------------------------------- queries
    def busy(self, day: date) -> set[tuple[str, int]]:
        rows = self.conn.execute("SELECT technician, start_hour FROM bookings WHERE day=? AND status='booked'",
                                 (day.isoformat(),)).fetchall()
        return {(r["technician"], r["start_hour"]) for r in rows}

    def free_technician(self, service: str, day: date, start_hour: int) -> str | None:
        taken = self.busy(day)
        for tech in BUSINESS["technicians"]:
            if service in tech["skills"] and (tech["name"], start_hour) not in taken:
                return tech["name"]
        return None

    def find_slots(self, service: str, now: datetime, day: date | None = None,
                   hours: tuple[int, int] | None = None, limit: int = 2, horizon_days: int = 14) -> list[dict]:
        """Earliest open windows matching the caller's day and time preferences."""
        days = [day] if day else [now.date() + timedelta(days=i) for i in range(horizon_days)]
        slots = []
        for d in days:
            if d < now.date() or d.weekday() not in BUSINESS["workdays"]:
                continue
            for start, end in BUSINESS["windows"]:
                if d == now.date() and start <= now.hour:
                    continue
                if hours and not (start >= hours[0] and start < hours[1]):
                    continue
                tech = self.free_technician(service, d, start)
                if tech:
                    slots.append({"day": d.isoformat(), "start_hour": start, "technician": tech,
                                  "label": f"{day_label(d, now.date())} between {window_label(start)}"})
                    if len(slots) >= limit:
                        return slots
        return slots

    # ---------------------------------------------------------------- writes
    def book(self, slot: dict, service: str, customer: str, phone: str, address: str,
             emergency: bool = False, source: str = "ai-receptionist") -> dict:
        tech = self.free_technician(service, date.fromisoformat(slot["day"]), slot["start_hour"]) \
            if not emergency else slot["technician"]
        if not tech:
            raise ValueError("That window was just taken.")
        booking_id = "BK-" + uuid.uuid4().hex[:6].upper()
        self.conn.execute("INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,?, 'booked', ?, ?)",
                          (booking_id, slot["day"], slot["start_hour"], tech, service, customer, phone, address,
                           int(emergency), source, datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()
        return {"booking_id": booking_id, **slot, "technician": tech}

    def find_by_phone(self, phone: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM bookings WHERE phone=? AND status='booked' ORDER BY day, start_hour",
                                (phone,)).fetchone()
        return dict(row) if row else None

    def cancel(self, booking_id: str) -> None:
        self.conn.execute("UPDATE bookings SET status='cancelled' WHERE id=?", (booking_id,))
        self.conn.commit()

    def all(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM bookings ORDER BY day, start_hour")]


def seed_existing(cal: Calendar, start: date) -> None:
    """Pre-existing jobs so the schedule looks like a real working week."""
    jobs = [(0, 8, "Jake", "ac_tuneup"), (0, 10, "Jake", "hvac_repair"), (0, 8, "Maria", "drain_cleaning"),
            (0, 12, "Maria", "plumbing_repair"), (0, 14, "Dev", "electrical"), (1, 8, "Jake", "furnace_tuneup"),
            (1, 8, "Maria", "water_heater"), (1, 10, "Maria", "plumbing_repair"), (1, 10, "Dev", "hvac_repair"),
            (1, 14, "Jake", "hvac_repair"), (2, 8, "Dev", "furnace_tuneup"), (2, 12, "Maria", "drain_cleaning"),
            (2, 16, "Jake", "ac_tuneup"), (3, 10, "Maria", "plumbing_repair"), (3, 14, "Dev", "electrical"),
            (4, 8, "Jake", "furnace_tuneup"), (4, 12, "Maria", "water_heater"), (5, 10, "Dev", "hvac_repair")]
    for offset, hour, tech, service in jobs:
        d = start + timedelta(days=offset)
        cal.conn.execute("INSERT INTO bookings VALUES (?,?,?,?,?,?,?,?,0,'booked','office',?)",
                         ("BK-" + uuid.uuid4().hex[:6].upper(), d.isoformat(), hour, tech, service,
                          "Existing customer", "", "", datetime.now().isoformat(timespec="seconds")))
    cal.conn.commit()
