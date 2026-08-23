"""Aircraft identity + interest classification.

Turns a bare 24-bit ICAO hex (icao24) into a rich aircraft record:
    registration, type, model, operator, category, and interest tags.

Two layers:
  1. Identity lookup  -> from the `aircraft` table (seeded from OpenSky's
     downloadable aircraftDatabase.csv, or enriched live).
  2. Interest scoring -> a rules engine that flags inherently interesting
     airframes (special liveries, warbirds, tankers, testbeds, gov/military,
     large private jets) independent of how rare they are at an airport.

This is the curated layer that is JetTip's real moat: a feed only gives you a
hex + tail + type; knowing that N705GT wears a retro livery, or that a given
C-17 is a notable visitor, requires a maintained knowledge base.
"""
import re
from . import db

# Categories that JetTip hides but we choose to surface (blocked/interesting).
BLOCKED_BUT_INTERESTING = {"military", "gov", "warbird", "tanker", "testbed"}

# Type-code prefixes -> (category, human label) for common notable airframes.
# Not exhaustive; the seed DB carries the authoritative per-airframe data and
# this is the fallback heuristic for unseen hexes.
TYPE_HEURISTICS = [
    (re.compile(r"^(C17|C5|C130|KC135|KC46|C40|E3|E6|P8|B52|F15|F16|F18|F22|F35|A10|U2|B1|B2)"), "military"),
    (re.compile(r"^(BE20|C130|DC10|B747|MD11)$"), None),  # placeholder, handled below
    (re.compile(r"^(SPIT|P51|P40|B17|B25|DC3|C47|T6|MUST|CORS)"), "warbird"),
    (re.compile(r"^(CL4|B34|DC10|MD87|B73)$"), "tanker"),  # firefighting tankers, curated
]

# Military-ish registration / callsign patterns (US + common allied).
MIL_CALLSIGN = re.compile(r"^(RCH|REACH|EVAC|SPAR|VADER|SLAM|GRIM|POLO|CNV|NAVY|ARMY|CFC|CANFORCE)", re.I)

# Large private-jet type codes worth flagging.
LARGE_PRIVATE = {"GLF6", "GLF5", "GLF4", "GL7T", "GL5T", "GL6T", "GLEX", "G650",
                 "FA7X", "FA8X", "F900", "CL60", "CL30", "CL35", "E55P", "BBJ",
                 "B737", "B738", "A319", "BCS3"}  # BBJ/ACJ-class when private

CARGO_OPERATORS = {"FDX", "UPS", "GTI", "ABX", "CLX", "GEC", "BOX", "CKS", "NCA"}


def get_aircraft(icao24: str) -> dict:
    """Return an aircraft dict, looking up the DB and falling back to defaults."""
    icao24 = (icao24 or "").lower().strip()
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM aircraft WHERE icao24 = ?", (icao24,)).fetchone()
    if row:
        d = dict(row)
    else:
        d = {
            "icao24": icao24, "registration": None, "typecode": None,
            "model": None, "operator": None, "category": "unknown",
            "interest_tags": "", "base_interest": 0,
        }
    return d


def classify(ac: dict, callsign: str | None = None) -> dict:
    """Augment an aircraft record with derived interest signals.

    Returns the record with possibly-updated category, interest_tags (list),
    base_interest (bool), and is_blocked (would JetTip have hidden this?).
    """
    tags = set(t for t in (ac.get("interest_tags") or "").split(",") if t)
    category = ac.get("category") or "unknown"
    typecode = (ac.get("typecode") or "").upper()
    base_interest = bool(ac.get("base_interest"))

    # Heuristic category inference when the DB didn't classify it.
    if category in ("unknown", None):
        for rx, cat in TYPE_HEURISTICS:
            if cat and rx.match(typecode):
                category = cat
                break
    if callsign and MIL_CALLSIGN.match(callsign):
        category = "military"
    if typecode in LARGE_PRIVATE and category in ("unknown", "private", "ga"):
        category = "private"
        tags.add("large-private")

    # Interest flags.
    if category in BLOCKED_BUT_INTERESTING:
        base_interest = True
        tags.add(category)
    if "large-private" in tags:
        base_interest = True

    is_blocked = category in BLOCKED_BUT_INTERESTING or "blocked" in tags

    ac = dict(ac)
    ac["category"] = category
    ac["interest_tags"] = sorted(tags)
    ac["base_interest"] = int(base_interest)
    ac["is_blocked"] = is_blocked
    return ac


def display_name(ac: dict) -> str:
    reg = ac.get("registration") or ac.get("icao24", "").upper()
    model = ac.get("model") or ac.get("typecode") or "Unknown type"
    op = ac.get("operator")
    return f"{reg} · {model}" + (f" · {op}" if op else "")
