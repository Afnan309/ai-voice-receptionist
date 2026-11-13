"""Emergency triage. Rule-based on purpose: safety decisions must be predictable and auditable."""
from __future__ import annotations

import re

RULES = [
    # level, reason, patterns, caller instructions
    ("life_safety", "possible gas leak",
     [r"\bsmell(s|ing)? (of )?gas\b", r"\bgas (leak|smell)\b", r"\brotten eggs?\b", r"\bsmells? like gas\b"],
     "Please leave the house right now, don't switch any lights or appliances on or off, and call 911 or the gas "
     "company from outside. I'm alerting our on-call technician now."),
    ("life_safety", "carbon monoxide alarm",
     [r"\bcarbon monoxide\b", r"\bco (alarm|detector)\b"],
     "Please get everyone outside into fresh air and call 911. I'm alerting our on-call technician now."),
    ("life_safety", "electrical fire risk",
     [r"\bsparks?\b", r"\bsparking\b", r"\bburning smell\b", r"\bsmoke\b", r"\bpanel is (hot|smoking)\b"],
     "If you see smoke or flames, leave and call 911. If it's safe, switch off the main breaker. I'm alerting our "
     "on-call technician now."),
    ("urgent", "active water leak",
     [r"\bburst\b", r"\bflood(ing|ed)?\b", r"\bwater (is )?(everywhere|pouring|gushing)\b", r"\bsewage\b",
      r"\bpipe (broke|broken|burst)\b", r"\bleaking (badly|everywhere)\b"],
     "If you can, turn off the main water shut-off valve, usually near the water heater or where the line enters "
     "the house. I'll get a technician out to you as fast as possible."),
    ("urgent", "loss of heat in cold weather",
     [r"\bno heat\b", r"\bfurnace (is )?(dead|out|not working|stopped|died)\b", r"\bheat(er)? (is )?(out|not working|stopped|died)\b",
      r"\bfreezing\b.*\b(house|home|inside)\b", r"\b(house|home) is freezing\b"],
     "I'm sorry, that's no fun in this weather. I'll get a technician out to you as fast as possible."),
]


def triage(text: str) -> dict | None:
    t = text.lower()
    for level, reason, patterns, instructions in RULES:
        if any(re.search(p, t) for p in patterns):
            return {"level": level, "reason": reason, "instructions": instructions}
    return None
