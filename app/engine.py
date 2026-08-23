"""Rarity + alert engine — the core of the JetTip clone.

Given observed visit events, decide which are 'unusual' at their airport and
generate alerts, mirroring JetTip's model:

  * RED  (high priority): airframe has <= RED_MAX_VISITS visits at this airport
                          within the trailing window (default: 2 in 30 days).
  * BLUE (low priority):  the 3rd..BLUE_MAX_VISITS visit within the window.
  * Inherently interesting airframes (special livery, warbird, tanker, testbed,
    military/gov, large private jet) always alert RED regardless of frequency.

Beyond JetTip: because we ingest unfiltered feeds, blocked military/gov/private
frames are included rather than dropped.
"""
import time
from . import db, config, aircraft


def record_visit(conn, ev: dict):
    """Insert a visit event (idempotent) and return True if newly inserted."""
    cur = conn.execute(
        """INSERT OR IGNORE INTO visits
           (airport_icao, icao24, direction, callsign, event_time)
           VALUES (?,?,?,?,?)""",
        (ev["airport_icao"], ev["icao24"], ev["direction"],
         ev.get("callsign"), ev["event_time"]),
    )
    return cur.rowcount > 0


def visit_count(conn, airport_icao: str, icao24: str, before_ts: int) -> int:
    """Count distinct visits (arr+dep of one trip collapsed) by this airframe at
    this airport within the trailing RARITY_WINDOW_DAYS.

    The window is anchored at max(event_time, now): for a live feed the event is
    ~now so this is a true trailing window, and for backfilled/historical events
    it measures recent frequency rather than penalising an airframe's earliest
    seen visits (which would otherwise always look 'rare' before history exists).
    """
    reference = max(before_ts, int(time.time()))
    window_start = reference - config.RARITY_WINDOW_DAYS * 86400
    rows = conn.execute(
        """SELECT event_time FROM visits
           WHERE airport_icao=? AND icao24=? AND event_time<=? AND event_time>=?
           ORDER BY event_time""",
        (airport_icao, icao24, reference, window_start),
    ).fetchall()
    # Collapse events within the same ~6h to one 'visit' (arr+dep of one trip).
    visits = 0
    last = None
    for r in rows:
        t = r["event_time"]
        if last is None or (t - last) > 6 * 3600:
            visits += 1
        last = t
    return visits


def is_diversion(conn, ev: dict, ac: dict) -> bool:
    """Heuristic: a scheduled operator arriving where it (almost) never operates.

    True when an airliner/cargo operator lands at an airport it serves rarely
    (<=1 arrival in the window) while being an active carrier elsewhere on the
    network (>=3 arrivals at other covered airports). That mismatch is the
    signature of a diversion. JetTip has no diversion detection of this kind.
    """
    if ev.get("direction") != "arrival":
        return False
    op = (ac.get("operator") or "").strip()
    if not op or ac.get("category") not in ("airliner", "cargo"):
        return False
    if "blocked" in op.lower() or op.lower() == "private":
        return False
    ref = max(ev["event_time"], int(time.time()))
    window_start = ref - config.RARITY_WINDOW_DAYS * 86400
    here = conn.execute(
        """SELECT COUNT(*) FROM visits v JOIN aircraft a ON a.icao24 = v.icao24
           WHERE a.operator=? AND v.airport_icao=? AND v.direction='arrival'
             AND v.event_time>=? AND v.event_time<=?""",
        (op, ev["airport_icao"], window_start, ref)).fetchone()[0]
    elsewhere = conn.execute(
        """SELECT COUNT(*) FROM visits v JOIN aircraft a ON a.icao24 = v.icao24
           WHERE a.operator=? AND v.airport_icao!=? AND v.direction='arrival'
             AND v.event_time>=? AND v.event_time<=?""",
        (op, ev["airport_icao"], window_start, ref)).fetchone()[0]
    return here <= 1 and elsewhere >= 3


def evaluate(conn, ev: dict) -> dict | None:
    """Evaluate one visit event; create an alert if it qualifies. Returns the
    alert dict or None."""
    ac = aircraft.classify(aircraft.get_aircraft(ev["icao24"]), ev.get("callsign"))
    count = visit_count(conn, ev["airport_icao"], ev["icao24"], ev["event_time"])

    priority = None
    reason = None
    diversion = is_diversion(conn, ev, ac)

    # First-ever sighting at this airport? (holy-grail for spotters — JetTip
    # has no explicit equivalent). "Ever" = no prior visit before this event.
    prior_ever = conn.execute(
        "SELECT 1 FROM visits WHERE airport_icao=? AND icao24=? AND event_time<? LIMIT 1",
        (ev["airport_icao"], ev["icao24"], ev["event_time"]),
    ).fetchone()
    # Gate first-ever on low frequency so high-frequency airliners (whose very
    # first backfilled event also has no prior) are not falsely flagged.
    first_ever = (prior_ever is None) and (count <= config.BLUE_MAX_VISITS)

    if first_ever:
        priority = "red"
        reason = "FIRST-EVER visit to this airport"
    elif ac["base_interest"]:
        priority = "red"
        tag = ", ".join(ac["interest_tags"]) or ac["category"]
        reason = f"Inherently notable airframe ({tag})"
    elif count <= config.RED_MAX_VISITS:
        priority = "red"
        reason = f"Rare visitor — {count} visit(s) in {config.RARITY_WINDOW_DAYS}d"
    elif count <= config.BLUE_MAX_VISITS:
        priority = "blue"
        reason = f"Uncommon visitor — visit #{count} in {config.RARITY_WINDOW_DAYS}d"
    elif diversion:
        priority = "red"  # diversion is alert-worthy on its own
        reason = ""
    else:
        return None  # regular traffic, no alert

    if diversion:
        op = ac.get("operator") or "operator"
        reason = f"DIVERSION — {op} rarely serves {ev['airport_icao']}" + (f" · {reason}" if reason else "")

    now = int(time.time())
    cur = conn.execute(
        """INSERT OR IGNORE INTO alerts
           (airport_icao, icao24, direction, callsign, event_time,
            priority, reason, visit_count, created_at, diversion)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (ev["airport_icao"], ev["icao24"], ev["direction"], ev.get("callsign"),
         ev["event_time"], priority, reason, count, now, int(diversion)),
    )
    if cur.rowcount == 0:
        return None
    return {
        "airport_icao": ev["airport_icao"], "icao24": ev["icao24"],
        "direction": ev["direction"], "event_time": ev["event_time"],
        "priority": priority, "reason": reason, "visit_count": count,
        "diversion": diversion, "aircraft": aircraft.display_name(ac),
    }


def ingest_events(events) -> dict:
    """Persist then evaluate a batch of events. Returns a summary.

    Two passes on purpose: ALL visits are inserted first so that rarity is
    computed against the complete trailing window. Evaluating incrementally as
    each row is inserted would make an airframe's earliest events look rare
    before its history exists — a backfill artifact, not real rarity.
    """
    new_visits = 0
    new_alerts = 0
    with db.get_conn() as conn:
        inserted = []
        for ev in events:
            if not ev.get("icao24"):
                continue
            if record_visit(conn, ev):
                new_visits += 1
                inserted.append(ev)
        for ev in inserted:
            if evaluate(conn, ev):
                new_alerts += 1
    return {"new_visits": new_visits, "new_alerts": new_alerts}
