"""Understanding the caller: intent classifier + entity extractors (service, day, time, name, phone, address).

In production on Vapi, the LLM handles open conversation and calls the tools in tools.py;
this NLU powers the local simulator, the evaluation and the transcript analytics.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, make_pipeline

ROOT = Path(__file__).resolve().parents[1]
BUSINESS = json.loads((ROOT / "data" / "business.json").read_text())

# ------------------------------------------------------------------ intents
_rows = json.loads((ROOT / "data" / "intent_train.json").read_text())
_X = [t for k, v in _rows.items() for t in v]
_y = [k for k, v in _rows.items() for _ in v]
_clf = make_pipeline(
    FeatureUnion([("w", TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)),
                  ("c", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True))]),
    LogisticRegression(C=10, max_iter=2000, class_weight="balanced"),
).fit(_X, _y)


def intent(text: str) -> tuple[str, float]:
    probs = _clf.predict_proba([text])[0]
    i = probs.argmax()
    return str(_clf.classes_[i]), float(probs[i])


# ------------------------------------------------------------------ service
SERVICE_PATTERNS = [
    ("ac_tuneup", r"\b(ac|a/c|air condition\w*|cooling)\b.*\b(tune[- ]?up|maintenance|check[- ]?up)\b|\b(tune[- ]?up|maintenance)\b.*\b(ac|a/c|air condition\w*)\b"),
    ("furnace_tuneup", r"\b(furnace|heater|heating)\b.*\b(tune[- ]?up|maintenance|check[- ]?up)\b|\b(tune[- ]?up|maintenance)\b.*\b(furnace|heat\w*)\b"),
    ("water_heater", r"\bwater heater\b|\bno hot water\b|\bhot water\b"),
    ("drain_cleaning", r"\bdrains?\b|\bclog\w*|\bbacked up\b|\bwon'?t drain\b"),
    ("electrical", r"\b(electric\w*|outlet\w*|breaker\w*|panel|wiring|light switch|ev charger|sparks?|sparking|flickering)\b"),
    ("hvac_repair", r"\b(ac|a/c|air condition\w*|furnace|heat|heating|hvac|thermostat|heat pump|cold air|warm air|no heat|cooling|blowing)\b"),
    ("plumbing_repair", r"\b(plumb\w*|leak\w*|pipes?|toilet|faucet|burst|flood\w*|sewage|sewer|water (is )?(everywhere|pouring))\b"),
]


def extract_service(text: str) -> str | None:
    t = text.lower()
    for service, pattern in SERVICE_PATTERNS:
        if re.search(pattern, t):
            return service
    return None


# ------------------------------------------------------------------ dates & times
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"]


def extract_day(text: str, today: date) -> date | None:
    t = text.lower()
    if re.search(r"\btoday\b|\bthis (morning|afternoon|evening)\b|\bright away\b|\bas soon as possible\b|\basap\b", t):
        return today
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)
    m = re.search(r"\b(next )?(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        target = WEEKDAYS.index(m.group(2))
        delta = (target - today.weekday()) % 7 or 7          # next occurrence
        if m.group(1) and target > today.weekday():          # "next Friday" said on a Monday = the week after
            delta += 7
        return today + timedelta(days=delta)
    m = re.search(r"\b(" + "|".join(MONTHS) + r")\s+(\d{1,2})(st|nd|rd|th)?\b", t)
    if m:
        d = date(today.year, MONTHS.index(m.group(1)) + 1, int(m.group(2)))
        return d if d >= today else date(today.year + 1, d.month, d.day)
    m = re.search(r"\b(\d{1,2})/(\d{1,2})\b", t)
    if m:
        d = date(today.year, int(m.group(1)), int(m.group(2)))
        return d if d >= today else date(today.year + 1, d.month, d.day)
    m = re.search(r"\bthe (\d{1,2})(st|nd|rd|th)\b", t)
    if m:
        day = int(m.group(1))
        d = date(today.year, today.month, day)
        if d < today:
            d = date(today.year + (today.month == 12), today.month % 12 + 1, day)
        return d
    return None


def extract_hours(text: str) -> tuple[int, int] | None:
    t = text.lower()
    m = re.search(r"\bafter (\d{1,2})\s*(am|pm)?", t)
    if m:
        h = int(m.group(1)) + (12 if (m.group(2) == "pm" or (not m.group(2) and int(m.group(1)) < 7)) else 0)
        return (h, 18)
    m = re.search(r"\b(?:at|around|by)\s+(\d{1,2})(?::\d{2})?\s*(am|pm|a\.m\.|p\.m\.)?", t) or \
        re.search(r"\b(\d{1,2})(?::\d{2})?\s*(am|pm|a\.m\.|p\.m\.)", t)
    if m:
        h = int(m.group(1))
        ampm = (m.group(2) or "").replace(".", "")
        if ampm == "pm" and h < 12:
            h += 12
        elif not ampm and h < 7:
            h += 12
        start = max(8, (h // 2) * 2)          # window containing that hour
        return (start, start + 1)
    if "morning" in t:
        return (8, 12)
    if "afternoon" in t:
        return (12, 16)
    if "evening" in t or "after work" in t or "late" in t:
        return (16, 18)
    return None


# ------------------------------------------------------------------ contact details
NOT_NAME = set(WEEKDAYS) | {"after", "before", "on", "at", "morning", "afternoon", "evening", "today", "tomorrow",
                             "yes", "no", "sure", "first", "second", "one", "the", "please", "earliest", "anytime"}


def extract_name(text: str) -> str | None:
    m = re.search(r"(?i:\b(?:my name is|my name's|this is|i'm|i am|it's|name is))\s+([A-Z][A-Za-z'\-]+(?:\s+[A-Z][A-Za-z'\-]+)?)", text)
    if m and m.group(1).split()[0].lower() not in NOT_NAME | {"calling", "having", "looking", "not", "just", "a"}:
        return m.group(1)
    m = re.fullmatch(r"\s*([A-Z][A-Za-z'\-]+\s+[A-Z][A-Za-z'\-]+)\.?\s*", text)
    return m.group(1) if m and not ({w.lower() for w in m.group(1).split()} & NOT_NAME) else None


def extract_phone(text: str) -> str | None:
    digits = re.sub(r"\D", "", " ".join(re.findall(r"[\d\-\(\)\. ]{10,}", text)))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}" if len(digits) == 10 else None


STREET = r"\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Za-z0-9 ]{2,30}?\s+(?:st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|boulevard|ct|court|way|pl|place|pkwy|parkway|cir|circle)\b\.?"


def extract_address(text: str) -> dict | None:
    m = re.search(STREET, text, flags=re.I)
    z = re.search(r"\b(8\d{4})\b", text)
    if not m and not z:
        return None
    return {"street": m.group(0).strip().rstrip(".") if m else None, "zip": z.group(1) if z else None}


def in_service_area(zip_code: str | None) -> bool | None:
    return None if not zip_code else zip_code in BUSINESS["service_zips"]


# ------------------------------------------------------------------ confirmations & choices
def yes_no(text: str) -> bool | None:
    t = text.lower()
    if re.search(r"\b(no|nope|not really|neither|don't|doesn't work|can't)\b", t):
        return False
    if re.search(r"\b(yes|yeah|yep|sure|perfect|great|sounds good|that works|correct|ok|okay|book it|please do|let's do)\b", t):
        return True
    return None


def pick_slot(text: str, slots: list[dict]) -> int | None:
    t = text.lower()
    if re.search(r"\b(first|earlier|earliest|1st|former)\b", t):
        return 0
    if len(slots) > 1 and re.search(r"\b(second|later|last|2nd|latter|other one)\b", t):
        return 1
    for i, s in enumerate(slots):
        hour = s["start_hour"] % 12 or 12
        day_name = date.fromisoformat(s["day"]).strftime("%A").lower()
        if re.search(rf"\b{hour}\b", t) or day_name in t or s["label"].split(" between")[0].lower() in t:
            return i
    return None
