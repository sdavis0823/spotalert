"""Watchlists — follow a specific tail, type, or category across a region.

Track-by-type across a region ("alert me on ANY A380 landing in the PNW") is a
long-standing spotter request that JetTip's airport-centric model doesn't cover.
A watchlist entry matches independent of the rarity engine: if a matching frame
appears at an in-region airport, the owner is notified.
"""
import time
from . import db, aircraft as ac_mod


def add(email, match_type, value, region="any", label=""):
    assert match_type in ("tail", "type", "category")
    now = int(time.time())
    with db.get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO watchlists
               (email, match_type, value, region, label, active, created_at)
               VALUES (?,?,?,?,?,1,?)""",
            (email, match_type, value.upper() if match_type != "category" else value.lower(),
             region, label, now))
        row = conn.execute(
            "SELECT * FROM watchlists WHERE email=? AND match_type=? AND value=? AND region=?",
            (email, match_type,
             value.upper() if match_type != "category" else value.lower(), region)).fetchone()
    return dict(row) if row else None


def list_for(email):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM watchlists WHERE email=? ORDER BY id DESC",
                            (email,)).fetchall()
    return [dict(r) for r in rows]


def delete(email, wid):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM watchlists WHERE id=? AND email=?", (wid, email))


def _region_ok(region, airport_icao):
    if not region or region == "any":
        return True
    return airport_icao in [a.strip().upper() for a in region.split(",") if a.strip()]


def matches(conn, alert):
    """Return watchlist rows that match a given alert (its aircraft + airport)."""
    ac = ac_mod.classify(ac_mod.get_aircraft(alert["icao24"]))
    reg = (ac.get("registration") or "").upper()
    typ = (ac.get("typecode") or "").upper()
    cat = ac.get("category")
    hits = []
    for w in conn.execute("SELECT * FROM watchlists WHERE active=1").fetchall():
        w = dict(w)
        if not _region_ok(w["region"], alert["airport_icao"]):
            continue
        v = w["value"]
        if w["match_type"] == "tail" and reg and v == reg:
            hits.append(w)
        elif w["match_type"] == "type" and typ and v == typ:
            hits.append(w)
        elif w["match_type"] == "category" and cat and v == cat:
            hits.append(w)
    return hits
