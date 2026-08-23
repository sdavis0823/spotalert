"""Inbound ETA engine — predictive alerts (a feature JetTip lacks).

JetTip tells you an interesting aircraft visited *after* it lands. This module
uses live positions to warn you *before* it arrives, while it's still airborne
and inbound — the alert is only useful if the aircraft is still there to see.

Method (from a single live snapshot per airport):
  * pull aircraft within a wide radius of the airport (airplanes.live),
  * keep those that are airborne, moving, and heading roughly toward the field
    (track bearing within HEADING_TOL of the bearing to the airport),
  * ETA = distance_nm / groundspeed_kts * 60 minutes,
  * emit an 'inbound' alert for notable/rare frames with ETA <= ETA_MAX_MIN.

Inbound alerts are stored in the alerts table with direction='inbound' and an
eta_minutes value. They are inherently transient predictions, so each refresh
re-stamps them at the current minute bucket (dedup by icao24+airport+minute).
"""
import math
import time
from . import db, config, sources, aircraft as ac_mod, engine

ETA_MAX_MIN = 90          # only alert on frames arriving within this window
HEADING_TOL = 55          # degrees; how aligned track must be with bearing-to-field
SNAPSHOT_RADIUS_NM = 250  # how far out to look for inbounds
MIN_GS = 80               # kts; ignore near-stationary/ground contacts


def _bearing(lat1, lon1, lat2, lon2):
    """Initial bearing from point1 -> point2, degrees 0..360."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _angdiff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def compute_inbounds(airport, contacts):
    """Pure function: given an airport row and a list of live contacts, return
    inbound candidates with computed ETA. Testable without network."""
    out = []
    alat, alon = airport["lat"], airport["lon"]
    for c in contacts:
        if c.get("lat") is None or c.get("lon") is None:
            continue
        gs = c.get("gs") or 0
        alt = c.get("alt")
        if alt == "ground" or gs < MIN_GS:
            continue
        dist = sources.haversine_nm(alat, alon, c["lat"], c["lon"])
        if dist < 3 or dist > SNAPSHOT_RADIUS_NM:
            continue
        track = c.get("track")
        brg = _bearing(c["lat"], c["lon"], alat, alon)
        if track is not None and _angdiff(track, brg) > HEADING_TOL:
            continue  # not heading toward the field
        eta = round(dist / gs * 60)
        if eta > ETA_MAX_MIN:
            continue
        out.append({"icao24": c["icao24"], "callsign": c.get("callsign"),
                    "dist_nm": round(dist), "gs": round(gs), "eta_min": eta})
    out.sort(key=lambda x: x["eta_min"])
    return out


def _is_notable(icao24):
    ac = ac_mod.classify(ac_mod.get_aircraft(icao24))
    return bool(
        ac["base_interest"]
        or ac.get("category") in ("military", "gov", "warbird", "tanker",
                                   "testbed", "special", "cargo")
        or ac.get("interest_tags")            # e.g. 'widebody-rare'
    )


def store_inbound(conn, airport_icao, cand):
    """Insert/refresh an inbound alert. Dedup at minute granularity so a frame
    re-detected on the next poll updates rather than spams."""
    now = int(time.time())
    minute_bucket = now - (now % 60)
    ac = ac_mod.classify(ac_mod.get_aircraft(cand["icao24"]))
    tag = ", ".join(ac["interest_tags"]) or ac.get("category") or "notable"
    reason = f"INBOUND — ETA ~{cand['eta_min']} min ({cand['dist_nm']} nm out, {cand['gs']} kt) · {tag}"
    cur = conn.execute(
        """INSERT OR IGNORE INTO alerts
           (airport_icao, icao24, direction, callsign, event_time,
            priority, reason, visit_count, created_at, eta_minutes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (airport_icao, cand["icao24"], "inbound", cand.get("callsign"),
         minute_bucket, "red", reason, 0, now, cand["eta_min"]))
    return cur.rowcount > 0


def scan_airport(conn, airport, contacts=None):
    """Scan one airport for notable inbounds. If contacts is None, pull live."""
    if contacts is None:
        alive = sources.AirplanesLiveSource()
        contacts = list(alive.fetch_snapshot(airport["lat"], airport["lon"],
                                              radius_nm=SNAPSHOT_RADIUS_NM))
    cands = [c for c in compute_inbounds(airport, contacts) if _is_notable(c["icao24"])]
    created = 0
    for c in cands:
        if store_inbound(conn, airport["icao"], c):
            created += 1
    return {"candidates": len(cands), "new_alerts": created, "detail": cands}


def scan_all(contacts_by_airport=None):
    """Scan every covered airport. contacts_by_airport lets tests/sim inject data."""
    result = {"new_alerts": 0, "by_airport": {}}
    with db.get_conn() as conn:
        airports = [dict(r) for r in conn.execute("SELECT * FROM airports").fetchall()]
        for ap in airports:
            if ap.get("lat") is None:
                continue
            contacts = None
            if contacts_by_airport is not None:
                contacts = contacts_by_airport.get(ap["icao"], [])
            r = scan_airport(conn, ap, contacts)
            result["new_alerts"] += r["new_alerts"]
            if r["candidates"]:
                result["by_airport"][ap["icao"]] = r["detail"]
    return result
