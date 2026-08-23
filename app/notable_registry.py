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

_PATH = os.path.join(os.path.dirname(__file__), "notable_registry.json")
_REG: dict = {}
_HEX: dict = {}


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
