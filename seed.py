"""Seed the database with covered airports, the curated aircraft knowledge base
(app/data.py — including blocked/notable frames JetTip hides), a synthetic-but-
plausible visit history, and a demo subscription so the notification pipeline
has someone to deliver to.

Run:  python -m seed
"""
import random
import time
from app import db, engine
from app.data import AIRPORTS, ALL_AIRCRAFT  # noqa: F401


def seed_airports(conn):
    for icao, iata, name, city, lat, lon in AIRPORTS:
        conn.execute(
            "INSERT OR REPLACE INTO airports (icao,iata,name,city,lat,lon) VALUES (?,?,?,?,?,?)",
            (icao, iata, name, city, lat, lon))


def seed_aircraft(conn):
    for row in ALL_AIRCRAFT:
        conn.execute(
            """INSERT OR REPLACE INTO aircraft
               (icao24,registration,typecode,model,operator,category,interest_tags,base_interest)
               VALUES (?,?,?,?,?,?,?,?)""", row)


def seed_subscription(conn):
    """A demo subscriber watching Seattle-area airports on all channels
    (email runs dry-run without SMTP; webhook left blank)."""
    conn.execute(
        """INSERT OR REPLACE INTO subscriptions
             (email,airports,want_red,want_blue,want_diversions,want_email,webhook_url,categories,active)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("spotter@example.com", "KSEA,KBFI,KPAE,KLAX", 1, 1, 1, 1, "", "", 1))


def seed_watchlist_and_log(conn):
    now = int(time.time())
    # demo watchlists: follow Air Force One anywhere, any A380 in the PNW,
    # and all warbirds at Seattle-area fields.
    for mt, val, region, label in [
        ("tail", "92-9000", "any", "Air Force One (anywhere)"),
        ("type", "A388", "KSEA,KBFI,KPAE,KVNY,CYVR", "Any A380 in the PNW/CA"),
        ("category", "warbird", "KSEA,KBFI,KPAE", "Warbirds near Seattle"),
    ]:
        conn.execute(
            """INSERT OR IGNORE INTO watchlists
               (email,match_type,value,region,label,active,created_at)
               VALUES (?,?,?,?,?,1,?)""",
            ("spotter@example.com", mt, val, region, label, now))
    # a couple of logbook entries so stats render. Look reg/type up straight from
    # the seed data (the aircraft rows aren't committed yet in this transaction).
    by_hex = {a[0]: a for a in ALL_AIRCRAFT}
    for icao24, ap, note in [("a4e001", "KBFI", "Caught the P-51 on a sunny evening"),
                             ("a2c001", "KSEA", "Alaska 'More To Love' livery")]:
        row = by_hex[icao24]
        conn.execute(
            """INSERT INTO logbook
               (email,icao24,registration,typecode,airport_icao,seen_at,notes,photo_url,created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            ("spotter@example.com", icao24, row[1], row[2],
             ap, now - 86400, note, "", now))


def seed_visits():
    now = int(time.time())
    day = 86400
    rng = random.Random(42)
    events = []

    common = [a[0] for a in ALL_AIRCRAFT if a[5] == "airliner" and "rare" not in a[6]]
    notable = [a[0] for a in ALL_AIRCRAFT if a[7] == 1 or "rare" in a[6]]
    airports = [a[0] for a in AIRPORTS]

    # Common airliners: frequent visitors at big hubs (should NOT alert).
    for icao24 in common:
        for ap in ["KSEA", "KLAX", "KJFK", "KSFO"]:
            for d in range(0, 30):
                if rng.random() < 0.6:
                    t = now - d * day - rng.randint(0, day)
                    events.append({"airport_icao": ap, "icao24": icao24,
                                   "direction": "arrival", "callsign": None, "event_time": t})
                    events.append({"airport_icao": ap, "icao24": icao24,
                                   "direction": "departure", "callsign": None,
                                   "event_time": t + rng.randint(3600, 5 * 3600)})

    # Notable frames: 1-3 visits across airports over the window.
    for icao24 in notable:
        n = rng.randint(1, 3)
        for _ in range(n):
            ap = rng.choice(airports)
            t = now - rng.randint(0, 20) * day - rng.randint(0, day)
            events.append({"airport_icao": ap, "icao24": icao24,
                           "direction": "arrival", "callsign": None, "event_time": t})
            events.append({"airport_icao": ap, "icao24": icao24,
                           "direction": "departure", "callsign": None,
                           "event_time": t + rng.randint(3600, 6 * 3600)})

    events.sort(key=lambda e: e["event_time"])
    return engine.ingest_events(events)


def bootstrap_reference():
    """Idempotently ensure reference data exists (airports + curated aircraft),
    WITHOUT generating demo visit history. Called on app startup so a fresh
    production deploy is ready to receive real data from the scheduler/backfill.
    Returns True if it seeded (table was empty)."""
    with db.get_conn() as conn:
        has = conn.execute("SELECT 1 FROM airports LIMIT 1").fetchone()
        if has:
            return False
        seed_airports(conn)
        seed_aircraft(conn)
    return True


def main():
    db.init_db()
    with db.get_conn() as conn:
        seed_airports(conn)
        seed_aircraft(conn)
        seed_subscription(conn)
        seed_watchlist_and_log(conn)
    summary = seed_visits()
    print(f"Seeded {len(AIRPORTS)} airports, {len(ALL_AIRCRAFT)} aircraft, 1 demo subscription.")
    print(f"Generated visits -> {summary}")


if __name__ == "__main__":
    main()
