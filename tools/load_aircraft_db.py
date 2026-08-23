"""Load a real aircraft-identity database into SpotAlert.

Real ADS-B feeds give you a bare 24-bit ICAO hex. To turn that into a
registration / type / operator you need a reference database. OpenSky publishes
a free one you download once:

    https://opensky-network.org/datasets/metadata/aircraftDatabase.csv

Then:  python -m tools.load_aircraft_db /path/to/aircraftDatabase.csv

This populates the `aircraft` table so real alerts show "N788QC · CRJ-900 ·
SkyWest" instead of a hex. Interest tagging (special liveries, warbirds, etc.)
is a SEPARATE curated layer — this loader sets category='unknown'/base_interest=0
for everything; your curated entries in app/data.py are preserved (not overwritten)
because they use INSERT OR IGNORE ordering: curated rows are re-applied by seed.py
after this bulk load if you re-run it.

The CSV is large (~500k rows); this streams it in batches.
"""
import csv
import sys
from app import db


def load(path: str, batch: int = 5000) -> int:
    db.init_db()
    n = 0
    with db.get_conn() as conn, open(path, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        rows = []
        for r in reader:
            icao24 = (r.get("icao24") or "").strip().lower()
            if not icao24:
                continue
            reg = (r.get("registration") or "").strip() or None
            typecode = (r.get("typecode") or "").strip() or None
            model = (r.get("model") or "").strip() or None
            manuf = (r.get("manufacturername") or "").strip()
            if model and manuf and manuf.lower() not in model.lower():
                model = f"{manuf} {model}"
            operator = ((r.get("operator") or "").strip()
                        or (r.get("owner") or "").strip() or None)
            # crude category seed from operatorcallsign / owner; refine via curation
            rows.append((icao24, reg, typecode, model, operator))
            if len(rows) >= batch:
                _flush(conn, rows)
                n += len(rows)
                rows = []
        if rows:
            _flush(conn, rows)
            n += len(rows)
        # re-attach curated special liveries to the now-known tails
        from app import liveries
        matched = liveries.apply(conn)
        print(f"Re-applied curated special liveries to {matched} tails.")
    return n


def _flush(conn, rows):
    conn.executemany(
        """INSERT INTO aircraft (icao24, registration, typecode, model, operator,
                                 category, interest_tags, base_interest)
           VALUES (?,?,?,?,?, 'unknown', '', 0)
           ON CONFLICT(icao24) DO UPDATE SET
             registration=COALESCE(excluded.registration, aircraft.registration),
             typecode=COALESCE(excluded.typecode, aircraft.typecode),
             model=COALESCE(excluded.model, aircraft.model),
             operator=COALESCE(excluded.operator, aircraft.operator)
           WHERE aircraft.base_interest = 0""",  # never clobber curated notable frames
        rows)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tools.load_aircraft_db /path/to/aircraftDatabase.csv")
        raise SystemExit(1)
    total = load(sys.argv[1])
    print(f"Loaded/updated {total:,} aircraft records.")
