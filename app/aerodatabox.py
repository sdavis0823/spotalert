"""AeroDataBox schedule source — free-tier full airport board.

Returns the entire day's schedule for an airport (arrivals + departures) with a
generous free quota, so "Load full day" never runs out. Each flight carries
number, airline, aircraft type, route and scheduled times; registration is
included when AeroDataBox has it (mainly closer to departure), which lets the
curated livery database flag special paint. Day-ahead US tail numbers remain
sparse everywhere in the industry, so pre-flagging still leans on FlightAware.

The FIDS endpoint returns at most a 12-hour window, so a multi-day board is
fetched in 12h chunks, paced to the free tier's 1-request/second limit.
"""
import time
import calendar

import httpx

from . import config

_LAST_ERROR = None


def available() -> bool:
    return bool(config.ADB_API_KEY)


def last_error():
    return _LAST_ERROR


def _headers():
    return {"x-rapidapi-key": config.ADB_API_KEY,
            "x-rapidapi-host": config.ADB_HOST,
            "Accept": "application/json"}


def _to_epoch(s):
    """Parse AeroDataBox time strings to a UTC epoch. Handles '2026-08-25 14:20Z',
    ISO 8601 with offset, and the space/'T' separator variants."""
    if not s:
        return None
    s = s.strip()
    try:
        if s.endswith("Z"):
            t = time.strptime(s[:16].replace("T", " "), "%Y-%m-%d %H:%M")
            return calendar.timegm(t)
        # with timezone offset e.g. 2026-08-25 07:20-07:00
        core = s.replace("T", " ")[:16]
        t = time.strptime(core, "%Y-%m-%d %H:%M")
        base = calendar.timegm(t)
        tz = s[16:].strip()
        if tz and (tz[0] in "+-") and ":" in tz:
            sign = 1 if tz[0] == "-" else -1   # subtract offset to get UTC
            hh, mm = tz[1:].split(":")
            base += sign * (int(hh) * 3600 + int(mm) * 60)
        return base
    except (ValueError, TypeError):
        return None


def _local(ts):
    return time.strftime("%Y-%m-%dT%H:%M", time.gmtime(ts))


def _get(path, params):
    global _LAST_ERROR
    try:
        with httpx.Client(timeout=25, headers=_headers()) as c:
            r = c.get(f"{config.ADB_BASE}{path}", params=params)
            if r.status_code >= 400:
                _LAST_ERROR = f"HTTP {r.status_code}: {(r.text or '')[:160]}"
                return None
            _LAST_ERROR = None
            return r.json()
    except (httpx.HTTPError, ValueError) as e:  # noqa: BLE001
        _LAST_ERROR = f"{type(e).__name__}: {str(e)[:160]}"
        return None


def _parse(fl, direction):
    ac = fl.get("aircraft") or {}
    al = fl.get("airline") or {}
    mv = fl.get("movement") or {}
    apt = mv.get("airport") or {}
    sched = (mv.get("scheduledTime") or {})
    rev = (mv.get("revisedTime") or {})
    when = _to_epoch(sched.get("utc") or rev.get("utc") or sched.get("local"))
    code = apt.get("iata") or apt.get("icao")
    reg = (ac.get("reg") or "").strip().upper() or None
    return {
        "ident": (fl.get("number") or fl.get("callSign") or "").replace(" ", "") or None,
        "registration": reg,
        "type": (ac.get("model") or "").strip() or None,
        "origin": code if direction == "arrival" else None,
        "destination": code if direction == "departure" else None,
        "scheduled_on": when if direction == "arrival" else None,
        "estimated_on": when if direction == "arrival" else None,
        "scheduled_off": when if direction == "departure" else None,
        "estimated_off": when if direction == "departure" else None,
        "status": fl.get("status"),
        "operator": al.get("name"),
        "cancelled": (fl.get("status") or "").lower() == "canceled",
        "diverted": False,
    }


def fetch_schedule(icao: str, hours: int = 72):
    """(arrivals, departures) parsed flight dicts across the next `hours`."""
    now = int(time.time())
    # start ~20h in the past so the current day is complete (and the UTC-vs-local
    # window offset never clips early-morning flights), out to `hours` ahead.
    end = now + hours * 3600
    arrivals, departures = [], []
    t = now - 20 * 3600
    first = True
    while t < end:
        chunk_end = min(t + 12 * 3600, end)
        if not first:
            time.sleep(1.1)   # free tier: 1 request/second
        first = False
        path = f"/flights/airports/icao/{icao}/{_local(t)}/{_local(chunk_end)}"
        data = _get(path, {"direction": "Both", "withLeg": "false",
                           "withCancelled": "true", "withCodeshared": "true",
                           "withCargo": "true", "withPrivate": "true",
                           "withLocation": "false"})
        if data:
            for f in data.get("arrivals", []) or []:
                arrivals.append(_parse(f, "arrival"))
            for f in data.get("departures", []) or []:
                departures.append(_parse(f, "departure"))
        t = chunk_end
    return arrivals, departures


def flight_detail(ident: str) -> dict | None:
    """One-call detail for a flight number: aircraft reg/model + photo, gate/
    terminal, live status, route and times. Powers the tap-a-plane card."""
    if not ident:
        return None
    data = _get(f"/flights/number/{ident}",
                {"withAircraftImage": "true", "withLocation": "false",
                 "dateLocalRole": "Both"})
    if not data:
        return None
    fl = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else None)
    if not fl:
        return None
    ac = fl.get("aircraft") or {}
    al = fl.get("airline") or {}
    dep = fl.get("departure") or {}
    arr = fl.get("arrival") or {}
    img = (ac.get("image") or {})

    def _apt(m):
        a = (m.get("airport") or {})
        return a.get("iata") or a.get("icao") or a.get("shortName") or a.get("name")

    def _t(m):
        s = (m.get("scheduledTime") or {})
        return s.get("local") or s.get("utc")
    return {
        "ident": fl.get("number") or ident,
        "airline": al.get("name"),
        "status": fl.get("status"),
        "registration": ac.get("reg"),
        "model": ac.get("model"),
        "image_url": img.get("url"),
        "image_credit": (img.get("author") or ""),
        "image_link": img.get("webUrl") or img.get("link"),
        "origin": _apt(dep), "destination": _apt(arr),
        "dep_terminal": dep.get("terminal"), "dep_gate": dep.get("gate"),
        "arr_terminal": arr.get("terminal"), "arr_gate": arr.get("gate"),
        "dep_time": _t(dep), "arr_time": _t(arr),
        "error": _LAST_ERROR,
    }


def load(icao: str, hours: int = 72) -> dict:
    """Fetch the full schedule and store it into scheduled_flights (the board
    cache). Returns counts. Cheap on the free tier (~1 call per 12h window)."""
    from . import db
    from . import flightaware as fa
    icao = icao.upper()
    arrivals, departures = fetch_schedule(icao, hours)
    now = int(time.time())
    horizon = now + hours * 3600
    with db.get_conn() as conn:
        stored = fa._store_scheduled_board(conn, icao, arrivals, departures, now, horizon)
    return {"stored": stored, "arrivals": len(arrivals), "departures": len(departures),
            "error": _LAST_ERROR}
