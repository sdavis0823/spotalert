"""Spotting logbook — the frames a user has personally caught / photographed.

Logbooks are the core of apps like TailTag and SpotBase; pairing one with the
alert engine ("saw the alert -> log the catch") is a combination neither JetTip
nor the pure-logbook apps offer.
"""
import time
from . import db, aircraft as ac_mod


def add(email, icao24=None, registration=None, typecode=None, airport_icao=None,
        seen_at=None, notes="", photo_url=""):
    now = int(time.time())
    seen_at = seen_at or now
    # Enrich from the knowledge base when only a hex is given.
    if icao24 and not (registration and typecode):
        ac = ac_mod.get_aircraft(icao24)
        registration = registration or ac.get("registration")
        typecode = typecode or ac.get("typecode")
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO logbook
               (email, icao24, registration, typecode, airport_icao, seen_at,
                notes, photo_url, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (email, icao24, registration, typecode, airport_icao, seen_at,
             notes, photo_url, now))
        return cur.lastrowid


def list_for(email, limit=500):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM logbook WHERE email=? ORDER BY seen_at DESC LIMIT ?",
            (email, limit)).fetchall()
    return [dict(r) for r in rows]


def delete(email, log_id):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM logbook WHERE id=? AND email=?", (log_id, email))


def stats(email):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM logbook WHERE email=?", (email,)).fetchall()
    tails = {r["registration"] for r in rows if r["registration"]}
    types = {r["typecode"] for r in rows if r["typecode"]}
    airports = {r["airport_icao"] for r in rows if r["airport_icao"]}
    return {
        "total": len(rows),
        "unique_tails": len(tails),
        "unique_types": len(types),
        "unique_airports": len(airports),
        "types": sorted(t for t in types if t),
    }
