"""Community notable-aircraft registry (Plane-Alert-DB).

~17k real special-interest aircraft keyed by registration and hex: military,
government / heads-of-state, historic & vintage, aerial firefighters, testbeds,
agency, celebrity and other distinctive frames. Sourced from the open
plane-alert-db project. This massively widens what we flag as "notable" beyond
aircraft *type* alone — matched by the specific tail number.

(Airline special *paint* liveries — retro schemes, anniversary jets — are the
one category no open dataset fully covers; those grow via user tagging.)
"""
import json
import os
import csv
import io

_PATH = os.path.join(os.path.dirname(__file__), "notable_registry.json")
_REG: dict = {}
_HEX: dict = {}

# Live source (community Plane-Alert-DB) for the weekly auto-refresh.
SOURCE_URL = os.environ.get(
    "PLANE_ALERT_DB_URL",
    "https://raw.githubusercontent.com/sdr-enthusiasts/plane-alert-db/main/plane-alert-db.csv")


def _cat_of(cmpg, category, tags):
    t = " ".join(tags).lower() + " " + (category or "").lower()
    if "firefighter" in t or "firefighting" in t or "tanker" in t:
        return "tanker"
    if "historic" in (category or "").lower() or "warbird" in t or "vintage" in t:
        return "warbird"
    if "test" in t:
        return "testbed"
    if cmpg == "Mil":
        return "military"
    if cmpg in ("Gov", "Pol"):
        return "gov"
    return "special"


def refresh_from_source() -> dict:
    """Re-download Plane-Alert-DB and rebuild the registry in memory. Keeps the
    military/gov/historic/etc. list current without a redeploy."""
    global _REG, _HEX
    import httpx
    try:
        with httpx.Client(timeout=30) as c:
            r = c.get(SOURCE_URL)
            r.raise_for_status()
            text = r.text
    except (httpx.HTTPError, ValueError) as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:120]}
    reg_idx = {}
    rows = list(csv.reader(io.StringIO(text)))[1:]
    for row in rows:
        if len(row) < 10:
            continue
        reg = (row[1] or "").strip().upper()
        if not reg:
            continue
        tags = [x.strip() for x in (row[6], row[7], row[8]) if x and x.strip()]
        reg_idx[reg] = {"h": (row[0] or "").strip().lower(),
                        "c": _cat_of((row[5] or "").strip(), (row[9] or "").strip(), tags),
                        "o": (row[2] or "").strip(), "t": (row[4] or "").strip(),
                        "g": tags[:2]}
    if len(reg_idx) < 500:  # sanity guard — don't clobber with a bad fetch
        return {"ok": False, "error": f"only {len(reg_idx)} rows parsed"}
    _REG = reg_idx
    _HEX = {v["h"]: reg for reg, v in _REG.items() if v.get("h")}
    return {"ok": True, "entries": len(_REG)}


def _load() -> None:
    global _REG, _HEX
    if _REG:
        return
    try:
        with open(_PATH, encoding="utf-8") as f:
            _REG = json.load(f)
    except (OSError, ValueError):
        _REG = {}
        return
    for reg, v in _REG.items():
        h = v.get("h")
        if h:
            _HEX[h] = reg


def count() -> int:
    _load()
    return len(_REG)


def by_reg(reg: str) -> dict | None:
    """Registry record for a registration, or None."""
    _load()
    return _REG.get((reg or "").upper().strip())


def by_hex(hexid: str):
    """(registration, record) for a 24-bit hex, or (None, None)."""
    _load()
    reg = _HEX.get((hexid or "").lower().strip())
    return (reg, _REG.get(reg)) if reg else (None, None)
