# SpotAlert — a JetTip clone (unusual-aircraft alerts)

Watches arrivals & departures at your airports and alerts you when an **unusual
or interesting** aircraft shows up — special liveries, warbirds, fire tankers,
testbeds, large private jets, and rare visitors — mirroring JetTip's model.

**Beyond JetTip:** it ingests *unfiltered* feeds, so it also surfaces the
**blocked military, government, and private jets that JetTip deliberately
hides** (LADD/PIA-blocked frames). Those are often the most interesting sightings.

## How it decides what's "unusual"
Same tiering as JetTip, plus extras JetTip doesn't have:

- **RED (high priority)** — airframe has ≤2 visits at this airport in the trailing 30 days.
- **BLUE (low priority)** — the 3rd–4th visit within 30 days.
- **Inherently notable** — special livery / warbird / tanker / testbed / military /
  gov / large private jet → always RED, regardless of frequency.
- **FIRST-EVER visit** — flags an airframe's first-ever appearance at an airport
  (the holy-grail sighting). JetTip has no explicit equivalent.

## Beyond JetTip (what this build adds)
- **Blocked frames included** — military, government, and LADD/PIA-blocked private
  jets that JetTip deliberately hides, via unfiltered feeds.
- **Predictive inbound ETA** — uses live positions to alert you *before* a notable
  frame lands ("A380 inbound, ETA ~10 min"), with a countdown. JetTip only tells
  you after the fact. Includes a `/api/eta/simulate` endpoint to test it on demand.
- **Live airframe photos** — each row shows the most recent photo of the *actual*
  tail, resolved through a provider chain: **Planespotters.net → airport-data.com**
  (by hex, falling back to registration), taking the first hit for maximum
  coverage. Photographer attribution + link back on every result; cached
  in-process. (JetPhotos has no open API and forbids scraping, so it's reached
  only indirectly via airport-data.com, a sanctioned aggregator.) Loads live on
  any normal network; some sandboxes/allowlists may block the photo hosts.
- **Watchlists / track-by-type** — follow a specific tail, a type, or a category
  across a region ("any A380 in the PNW"). JetTip is airport-only.
- **Spotting logbook** — log the frames you've caught, with per-user stats
  (unique tails/types/airports). Pairs the alert engine with a TailTag-style log.
- **Diversion alerts** — flags a scheduled carrier landing at an airport it
  rarely/never serves (the signature of a diversion). Opt-in per subscription.
  JetTip has no diversion detection. `/api/diversion/simulate` to test it.
- **Web push** — real browser/phone push via VAPID + service worker, so alerts
  arrive even with the tab closed. Optional `pywebpush` sends for real; without
  it, push runs in dry-run (logged), same safe pattern as email.
- **Real-time delivery** — a background scheduler polls every airport on an
  interval, re-evaluates, runs the ETA scan, and pushes alerts automatically.
- **Multi-channel** — email (SMTP, dry-run default), Slack/Discord/custom
  **webhooks**, and **web push** — with per-subscription category filters.
- **Idempotent notification log** — every dispatch recorded; no double-sends.
- **Curated knowledge base** — 56+ airframes across 12 airports (extensible).
- **Special-livery registry** — a growable list (`app/liveries.py`, keyed by
  registration) that tags known special/heritage/state liveries so they always
  push. Grow it live with `POST /api/aircraft/tag` as you spot new schemes; it
  re-attaches automatically when you load a fresh identity DB.

Rarity is computed per airframe (24-bit ICAO hex) per airport over a 30-day
window, with arrival+departure of one trip collapsed to a single "visit."

## Data sources (pluggable)
Chosen after a deep-dive into how JetTip works (it never publishes its source,
but honoring operator blocking points to a filtered commercial feed like
FlightAware). To be *fresher and more complete* than JetTip, this build uses:

- **OpenSky Network** (`app/sources.py` → `OpenSkySource`) — free
  `/flights/arrival` & `/flights/departure` airport boards + history. The
  structured backbone. Needs free OAuth2 client credentials.
- **airplanes.live** (`AirplanesLiveSource`) — free **unfiltered** live layer
  that catches blocked/military frames OpenSky and JetTip miss. No key needed.
- **FlightAware AeroAPI** — not included, but the adapter interface is designed
  so it drops in as another source for commercial-grade cleanliness.

Runs fully on bundled **seed data with no credentials** so you can demo it now.

## Run it
```bash
pip install -r requirements.txt
python -m seed                       # load airports + aircraft KB + demo history
uvicorn app.main:app --reload --port 8077
# open http://127.0.0.1:8077
```

### Go live (real, always-latest data)
```bash
export OPENSKY_CLIENT_ID=...         # from https://opensky-network.org (free)
export OPENSKY_CLIENT_SECRET=...
# then hit "Pull live data" in the UI, or:
curl -X POST "localhost:8077/api/refresh?hours=48"
```
airplanes.live works with no credentials, so even without OpenSky the "Pull
live data" button enriches the boards with current unfiltered traffic.

## API
| Method | Path | Purpose |
|---|---|---|
| GET | `/api/airports` | covered airports |
| GET | `/api/board/{icao}?hours=&notable_only=&direction=&category=` | flight board |
| GET | `/api/alerts?airport=&priority=&limit=` | alert feed |
| GET | `/api/aircraft/{icao24}` | aircraft identity + interest |
| GET | `/api/photo/{icao24}?reg=` | latest photo of the actual airframe (Planespotters) |
| POST | `/api/subscriptions` | create/update a subscription (airports, priorities, channels, category filter) |
| GET | `/api/subscriptions/{email}` | read a subscription |
| POST | `/api/refresh?hours=` | run one full cycle now (pull + evaluate + notify) |
| POST | `/api/notify/run` | dispatch notifications for any undelivered alerts |
| GET | `/api/notifications?email=` | notification delivery log |
| GET | `/api/scheduler` | background scheduler status |
| POST | `/api/eta/scan` | scan live traffic for notable inbounds |
| POST | `/api/eta/simulate` | inject a synthetic inbound to test ETA alerts |
| GET/POST/DELETE | `/api/watchlists[...]` | manage watchlists (tail/type/category × region) |
| GET/POST/DELETE | `/api/logbook[...]` | spotting logbook + stats |
| POST | `/api/diversion/simulate` | inject a diverted arrival to test detection |
| GET | `/api/push/vapid-public-key` | VAPID key + push capability status |
| POST | `/api/push/subscribe` | register a browser/device for push |
| POST | `/api/push/test/{email}` | send a test push |

## Notifications & scheduler
Runs a background loop every `SCHEDULER_INTERVAL_SEC` (default 300s). Email
delivery is **dry-run** (rendered + logged, nothing sent) until SMTP is set —
so the whole pipeline is testable with zero config and no risk of real mail:
```bash
export SMTP_HOST=smtp.example.com SMTP_USER=... SMTP_PASSWORD=...
export SCHEDULER_INTERVAL_SEC=120        # poll every 2 min
export NOTIFY_DRY_RUN=1                   # force dry-run even with SMTP set
```
Webhooks (Slack/Discord/custom) need no server config — set the URL per
subscription in the UI Settings tab.

## Layout
```
app/
  config.py     tuning + credentials (env-overridable)
  db.py         SQLite schema + connection
  aircraft.py   icao24 -> identity + interest classification (the curated moat)
  sources.py    pluggable feed adapters (OpenSky, airplanes.live)
  engine.py     rarity + alert engine (insert-then-evaluate over full window)
  main.py       FastAPI app + routes
  static/index.html   single-file web UI (boards + alert feed)
seed.py         airports, aircraft knowledge base, demo visit history
```

## Roadmap (community-informed)
Built this pass: predictive inbound ETA, watchlists/track-by-type, spotting
logbook. Still open, drawn from what spotters ask for (TailTag/SpotBase logs,
AI photo-ID apps, FlightAware "track by type" requests):
- **AI photo-ID** — "I have a photo but don't know what it is" → type/livery.
- **Diversion alerts** — aircraft landing somewhere unusual for its operator
  (opt-in flag already in the schema).
- **Web push (VAPID)** alongside email + webhook.
- **Photo uploads** on logbook entries (field already present).
```
