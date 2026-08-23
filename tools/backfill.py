"""Backfill real arrivals/departures for one airport and validate the output.

This is the "prove the pipeline on one real airport" step. It pulls actual
OpenSky history for an airport over N days, runs it through the real rarity +
alert engine, and prints a validation report so you can judge whether the alerts
are the ones you'd actually want.

Prereqs:
  1. Free OpenSky account -> create an API client:
       https://opensky-network.org/  (Account -> API Clients)
     export OPENSKY_CLIENT_ID=...        # NOT your password; a client id/secret
     export OPENSKY_CLIENT_SECRET=...    # keep secret; set as env vars, never commit
  2. (Recommended) load an identity DB first so alerts show tails/types:
       python -m tools.load_aircraft_db aircraftDatabase.csv
  3. (Optional) curate liveries/notability in app/data.py, then: python -m seed

Run:
  python -m tools.backfill KPHX --days 14

Notes:
  * OpenSky caps each call to a 2-day window; the source adapter windows it.
  * Rarity needs history: the more days you backfill, the more trustworthy the
    "unusual here?" verdict. 14-30 days is a sensible first pass.
"""
import argparse
import time
from collections import Counter

from app import db, engine, sources, aircraft as ac_mod
from app.data import AIRPORTS


def ensure_airport(icao):
    with db.get_conn() as conn:
        row = conn.execute("SELECT 1 FROM airports WHERE icao=?", (icao,)).fetchone()
        if row:
            return
        match = next((a for a in AIRPORTS if a[0] == icao), None)
        if not match:
            raise SystemExit(f"{icao} not in app/data.py AIRPORTS — add it (icao,iata,name,city,lat,lon).")
        conn.execute("INSERT OR REPLACE INTO airports (icao,iata,name,city,lat,lon) VALUES (?,?,?,?,?,?)", match)


def report(icao, days):
    now = int(time.time())
    since = now - days * 86400
    with db.get_conn() as conn:
        visits = conn.execute(
            "SELECT COUNT(*) FROM visits WHERE airport_icao=? AND event_time>=?",
            (icao, since)).fetchone()[0]
        alerts = [dict(r) for r in conn.execute(
            "SELECT * FROM alerts WHERE airport_icao=? AND event_time>=? ORDER BY event_time DESC",
            (icao, since)).fetchall()]
        distinct = conn.execute(
            "SELECT COUNT(DISTINCT icao24) FROM visits WHERE airport_icao=? AND event_time>=?",
            (icao, since)).fetchone()[0]

    by_prio = Counter(a["priority"] for a in alerts)
    identified = sum(1 for a in alerts if (ac_mod.get_aircraft(a["icao24"]) or {}).get("registration"))
    print("\n" + "=" * 66)
    print(f"  VALIDATION REPORT — {icao} — last {days} days")
    print("=" * 66)
    print(f"  visit events ingested : {visits:,}")
    print(f"  distinct airframes    : {distinct:,}")
    print(f"  alerts generated      : {len(alerts)}  (red {by_prio.get('red',0)}, blue {by_prio.get('blue',0)})")
    print(f"  alerts w/ known tail  : {identified}/{len(alerts)}"
          + ("  <- load an aircraft DB to raise this" if identified < len(alerts) else ""))
    print("-" * 66)
    print("  Most recent alerts:")
    for a in alerts[:20]:
        ac = ac_mod.classify(ac_mod.get_aircraft(a["icao24"]))
        name = ac_mod.display_name(ac)
        print(f"   [{a['priority']:<4}] {name[:52]:<52} {a['reason'][:40]}")
    if not alerts:
        print("   (none yet — backfill more days, or check OpenSky credentials/coverage)")
    print("=" * 66)
    print("  Judge it: are these the aircraft you'd want pinged about? If common")
    print("  airliners are showing as 'rare', backfill more days. If everything is")
    print("  a bare hex, load the aircraft identity DB. If liveries aren't flagged,")
    print("  that's the curated layer (app/data.py) — expected.\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("airport", help="ICAO code, e.g. KPHX")
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    icao = args.airport.upper()

    db.init_db()
    ensure_airport(icao)

    src = sources.OpenSkySource()
    if not src.available():
        raise SystemExit("OpenSky credentials not set. export OPENSKY_CLIENT_ID / OPENSKY_CLIENT_SECRET first.")

    now = int(time.time())
    begin = now - args.days * 86400
    print(f"Backfilling {icao} for {args.days} days from OpenSky … (this can take a minute)")
    summary = engine.ingest_events(src.fetch(icao, begin, now))
    print(f"Ingested: {summary}")
    report(icao, args.days)


if __name__ == "__main__":
    main()
