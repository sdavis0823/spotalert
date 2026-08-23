# Prove SpotAlert on real KPHX data — step by step

The goal: point the engine at **real Phoenix Sky Harbor traffic**, backfill a
couple of weeks, and judge whether the alerts are the ones you'd actually want.
Runs on your own machine — your OpenSky keys stay local, never in this repo.

## 1. Install
```bash
cd jettip
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m seed                      # loads KPHX + the curated airframes
```

## 2. Get free OpenSky API credentials (2 min)
1. Make a free account at https://opensky-network.org/
2. Account → **API Clients** → create a client. You get a **client id** and
   **client secret** (this is NOT your login password).
3. Set them as environment variables (keep them secret — don't commit them):
```bash
export OPENSKY_CLIENT_ID='your-client-id'
export OPENSKY_CLIENT_SECRET='your-client-secret'
```

## 3. (Recommended) Load an aircraft identity database
Without this, real alerts show a bare hex instead of a tail/type. Download
OpenSky's free reference DB once, then load it:
```bash
curl -L -o aircraftDatabase.csv \
  https://opensky-network.org/datasets/metadata/aircraftDatabase.csv
python -m tools.load_aircraft_db aircraftDatabase.csv
python -m seed          # re-apply curated notable frames on top
```

## 4. Backfill real KPHX history and read the report
```bash
python -m tools.backfill KPHX --days 14
```
You'll get a validation report: how many arrivals/departures were ingested, how
many alerts came out, how many resolved to a real tail, and the most recent
alerts with the reason each fired.

**How to judge it:**
- Common airliners showing up as "rare"? → backfill more days (`--days 30`).
  Rarity needs history before it's trustworthy.
- Alerts are bare hex codes? → you skipped step 3; load the identity DB.
- Special liveries not flagged? → expected. That's the curated layer
  (`app/data.py`) — the real ongoing work, same as JetTip's.

## 5. Run the live app
```bash
uvicorn app.main:app --port 8077        # scheduler polls KPHX automatically
# open http://127.0.0.1:8077  → airframe photos load live in your browser
```
Optional real web push: `pip install pywebpush`, then use Settings → Enable push.

---
This is the honest test. Features are done; this tells you whether the **data**
makes SpotAlert trustworthy for your field. Backfill, read the report, and we
tune the thresholds (`RED_MAX_VISITS`, `RARITY_WINDOW_DAYS` in `app/config.py`)
from what you see.
