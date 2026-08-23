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
