"""Background scheduler — turns SpotAlert from on-demand into always-on.

An asyncio loop that, every SCHEDULER_INTERVAL_SEC:
  1. pulls live data for every covered airport (OpenSky + airplanes.live),
  2. re-evaluates rarity and generates alerts,
  3. dispatches notifications to matching subscriptions.

Runs in-process with the FastAPI app (started on startup, stopped on shutdown).
Blocking network/DB work is pushed to a thread so it never stalls the event loop.
Exposes a small status object for the /api/scheduler endpoint.
"""
import asyncio
import time
import traceback

from . import config, db, engine, notify, sources, eta as eta_mod
from . import flightaware as fa_mod

_last_fa_scan = 0

STATE = {
    "enabled": config.SCHEDULER_ENABLED,
    "interval_sec": config.SCHEDULER_INTERVAL_SEC,
    "running": False,
    "runs": 0,
    "last_run": None,
    "last_result": None,
    "last_error": None,
}
_task: asyncio.Task | None = None


def _refresh_all(hours: int) -> dict:
    """Synchronous one-shot refresh of every airport + notification dispatch."""
    end = int(time.time())
    begin = end - hours * 3600
    opensky = sources.OpenSkySource()
    alive = sources.AirplanesLiveSource()
    totals = {"new_visits": 0, "new_alerts": 0, "sources": []}

    with db.get_conn() as conn:
        airports = [dict(r) for r in conn.execute("SELECT * FROM airports").fetchall()]

    if opensky.available():
        totals["sources"].append("opensky")
        for ap in airports:
            s = engine.ingest_events(opensky.fetch(ap["icao"], begin, end))
            totals["new_visits"] += s["new_visits"]
            totals["new_alerts"] += s["new_alerts"]

    totals["sources"].append("airplanes.live")
    for ap in airports:
        if ap.get("lat") is None:
            continue
        events = []
        for a in alive.fetch_snapshot(ap["lat"], ap["lon"], radius_nm=15):
            if a.get("lat") is None:
                continue
            dist = sources.haversine_nm(ap["lat"], ap["lon"], a["lat"], a["lon"])
            alt = a.get("alt")
            low = (alt == "ground") or (isinstance(alt, (int, float)) and alt < 4000)
            if dist <= 12 and low:
                events.append({"airport_icao": ap["icao"], "icao24": a["icao24"],
                               "direction": "arrival", "callsign": a.get("callsign"),
                               "event_time": end})
        s = engine.ingest_events(events)
        totals["new_visits"] += s["new_visits"]
        totals["new_alerts"] += s["new_alerts"]

    # predictive inbound ETA scan (live positions -> pre-arrival alerts)
    try:
        totals["inbound"] = eta_mod.scan_all()
    except Exception as e:  # noqa: BLE001
        totals["inbound"] = {"error": str(e)}

    # emergency squawk watch (free live feed) — 7500/7600/7700 near a field
    if config.EMERGENCY_SQUAWK_ENABLED:
        try:
            from . import live_extras
            emg = 0
            with db.get_conn() as conn:
                for ap in airports:
                    if ap.get("lat") is None:
                        continue
                    for e in live_extras.emergencies_near(ap["lat"], ap["lon"], 120):
                        reason = (f"EMERGENCY — {e['meaning']} (squawk {e['squawk']}) "
                                  f"{e.get('dist_nm','?')} nm from {ap['icao']} · "
                                  f"{e.get('reg') or e['icao24']} {e.get('type') or ''}".strip())
                        cur = conn.execute(
                            """INSERT OR IGNORE INTO alerts
                               (airport_icao, icao24, direction, callsign, event_time,
                                priority, reason, visit_count, created_at)
                               SELECT ?,?,?,?,?,?,?,?,?
                               WHERE NOT EXISTS (SELECT 1 FROM alerts WHERE airport_icao=?
                                 AND icao24=? AND direction='emergency' AND event_time > ?)""",
                            (ap["icao"], e["icao24"], "emergency", e.get("callsign"), end,
                             "red", reason, 0, end, ap["icao"], e["icao24"], end - 1800))
                        if cur.rowcount:
                            emg += 1
                conn.commit()
            totals["emergencies"] = emg
        except Exception as e:  # noqa: BLE001
            totals["emergencies"] = {"error": str(e)}

    # pre-takeoff / scheduled-arrival scan (FlightAware) — the paid call, so run
    # it on its own slower cadence, not every loop.
    global _last_fa_scan
    fa_src = fa_mod.FlightAwareSource()
    if fa_src.available() and fa_src.budget_ok() and (end - _last_fa_scan) >= config.FA_AIRPORT_SCAN_INTERVAL_SEC:
        _last_fa_scan = end
        sched = 0
        for ap in airports:
            if not fa_src.budget_ok():
                break  # hard stop before the free-tier cap
            try:
                sched += fa_mod.scan_airport_flights(ap["icao"])["new_alerts"]
            except Exception:  # noqa: BLE001
                pass
        totals["scheduled"] = sched
        totals["fa_budget_remaining"] = fa_mod.budget_remaining()

    totals["dispatch"] = notify.dispatch_new()
    return totals


def run_once(hours: int | None = None) -> dict:
    """Public sync entry (used by /api/refresh and by the loop)."""
    hours = hours or config.SCHEDULER_LOOKBACK_HOURS
    result = _refresh_all(hours)
    STATE["runs"] += 1
    STATE["last_run"] = int(time.time())
    STATE["last_result"] = result
    return result


async def _loop():
    STATE["running"] = True
    try:
        while True:
            try:
                await asyncio.to_thread(run_once)
                STATE["last_error"] = None
            except Exception:  # noqa: BLE001
                STATE["last_error"] = traceback.format_exc().splitlines()[-1]
            await asyncio.sleep(STATE["interval_sec"])
    finally:
        STATE["running"] = False


def start():
    global _task
    if not config.SCHEDULER_ENABLED:
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop())


async def stop():
    global _task
    if _task:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
