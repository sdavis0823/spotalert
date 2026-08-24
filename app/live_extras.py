"""Free live-feed extras — no API key, no query budget.

Three spotter features built on data that costs nothing:
  * emergency squawk watch (7500/7600/7700) near a point,
  * "overhead now" — what's flying above your GPS location right now,
  * airport weather + likely active runway, from aviationweather.gov METAR.
"""
import re
import time
import httpx
from . import sources, config, aircraft as ac_mod

# IATA flight-number prefix -> ICAO callsign prefix, so we can turn a schedule
# flight number (e.g. "JX26", Starlux) into the ADS-B callsign ("SJX26") that
# the live feed broadcasts. Covers the carriers that serve PHX; extend freely.
IATA2ICAO = {
    "AA": "AAL", "WN": "SWA", "DL": "DAL", "UA": "UAL", "AS": "ASA", "B6": "JBU",
    "NK": "NKS", "F9": "FFT", "HA": "HAL", "SY": "SCX", "G4": "AAY", "OO": "SKW",
    "MQ": "ENY", "YX": "RPA", "YV": "ASH", "9E": "EDV", "OH": "JIA", "ZW": "AWI",
    "GB": "ABX", "FX": "FDX", "5X": "UPS", "5Y": "GTI", "PT": "PDT",
    "JX": "SJX", "CI": "CAL", "BR": "EVA", "AC": "ACA", "WS": "WJA", "BA": "BAW",
    "DE": "CFG", "Y4": "VOI", "AM": "AMX", "LH": "DLH", "KL": "KLM", "JL": "JAL",
    "NH": "ANA", "KE": "KAL", "QF": "QFA", "NZ": "ANZ", "AV": "AVA", "CM": "CMP",
    "LA": "LAN", "AF": "AFR", "VB": "VIV", "4O": "AIJ", "WN ": "SWA",
}


def _ident_to_callsign(ident):
    """schedule flight number -> ADS-B callsign, or None."""
    s = (ident or "").upper().replace(" ", "")
    m = re.match(r"^([A-Z0-9]{2})(\d{1,4}[A-Z]?)$", s)
    if not m:
        # maybe already a 3-letter ICAO callsign like SWA123
        return s if re.match(r"^[A-Z]{3}\d", s) else None
    icao = IATA2ICAO.get(m.group(1))
    return (icao + m.group(2)) if icao else None


# Open ADS-B mirrors (re-api v2 format). airplanes.live now gates anonymous /
# datacenter access, so adsb.lol / adsb.fi are primary; airplanes.live is last.
_ADSB_BASES = [
    "https://api.adsb.lol/v2",
    "https://opendata.adsb.fi/api/v2",
    "https://api.airplanes.live/v2",
]


_TAIL_UA = "SpotAlert/1.0 (+https://spotalert.onrender.com)"

# One wide /point/ snapshot answers fast and covers everything airborne near the
# field, whereas per-/callsign/ queries are throttled to ~30s from datacenter
# IPs. So the ONLY ADS-B fetching happens in the background (scheduler ->
# warm_snapshot), and card opens are pure in-memory reads of this map. That keeps
# lookups instant AND stops user traffic from ever tripping the feed's rate limit.
_SNAP_CENTER = (33.4342, -112.0116)   # KPHX
_SNAP_RADIUS = 250                    # nm (mirror max)
_snap_cache = {"ts": 0.0, "map": {}}  # {CALLSIGN: REG}; last good snapshot kept


def _fetch_snapshot_map():
    """One bulk /point/ query around the home field -> {CALLSIGN: REG}, or {}."""
    lat, lon = _SNAP_CENTER
    for base in _ADSB_BASES:
        try:
            with httpx.Client(timeout=12, headers={"User-Agent": _TAIL_UA}) as c:
                r = c.get(f"{base}/point/{lat}/{lon}/{_SNAP_RADIUS}")
                if r.status_code != 200:
                    continue
                acs = r.json().get("ac") or []
        except (httpx.HTTPError, ValueError):
            continue
        if not acs:
            continue
        m = {}
        for a in acs:
            cs = (a.get("flight") or "").strip().upper()
            reg = (a.get("r") or "").strip().upper()
            if cs and reg:
                m[cs] = reg
        if m:
            return m
    return {}


def warm_snapshot():
    """Force-refresh the snapshot (called from the background scheduler so a
    user never pays the ~30s build). Safe to call often; no-op on empty fetch."""
    m = _fetch_snapshot_map()
    if m:
        _snap_cache.update(ts=time.time(), map=m)
    return len(_snap_cache["map"])


def tail_for_flight(ident):
    """Registration of the aircraft currently flying under this flight number.

    This is a PURE in-memory read of the background-maintained snapshot — it
    never makes a network call, so a card open is always instant and user
    traffic can never saturate the ADS-B feed's rate limit. The snapshot is
    refreshed in the background by the scheduler (see warm_snapshot). Returns
    None if the flight isn't in the latest snapshot (not airborne near the
    field, or callsign can't be mapped), in which case the card shows the
    type illustration.
    """
    cs = _ident_to_callsign(ident)
    if not cs:
        return None
    return _snap_cache["map"].get(cs)


# Emergency transponder codes and what they mean.
EMERGENCY_SQUAWKS = {
    "7500": "HIJACK",
    "7600": "RADIO FAILURE",
    "7700": "GENERAL EMERGENCY",
}


def emergencies_near(lat: float, lon: float, radius_nm: int = 120) -> list[dict]:
    """Aircraft squawking an emergency code within radius of a point."""
    alive = sources.AirplanesLiveSource()
    out = []
    for ac in alive.fetch_snapshot(lat, lon, radius_nm=radius_nm):
        sq = str(ac.get("squawk") or "").strip()
        if sq in EMERGENCY_SQUAWKS:
            out.append({
                "icao24": ac["icao24"], "callsign": ac.get("callsign"),
                "reg": ac.get("reg"), "type": ac.get("type"),
                "squawk": sq, "meaning": EMERGENCY_SQUAWKS[sq],
                "lat": ac.get("lat"), "lon": ac.get("lon"), "alt": ac.get("alt"),
                "dist_nm": (round(sources.haversine_nm(lat, lon, ac["lat"], ac["lon"]))
                            if ac.get("lat") is not None else None),
            })
    out.sort(key=lambda x: (x["dist_nm"] is None, x["dist_nm"]))
    return out


def overhead_now(lat: float, lon: float, radius_nm: int = 20) -> list[dict]:
    """Everything flying near a GPS point right now, notable ones flagged."""
    alive = sources.AirplanesLiveSource()
    out = []
    for c in alive.fetch_snapshot(lat, lon, radius_nm=radius_nm):
        if c.get("lat") is None:
            continue
        ac = ac_mod.classify(ac_mod.get_aircraft(c["icao24"]))
        notable = bool(ac.get("base_interest") or ac.get("interest_tags")
                       or c.get("military") or c.get("interesting"))
        out.append({
            "icao24": c["icao24"], "callsign": c.get("callsign"),
            "reg": c.get("reg") or ac.get("registration"),
            "type": c.get("type") or ac.get("typecode"),
            "alt": c.get("alt"), "gs": c.get("gs"),
            "dist_nm": round(sources.haversine_nm(lat, lon, c["lat"], c["lon"])),
            "military": bool(c.get("military")),
            "category": ac.get("category"),
            "interest_tags": ac.get("interest_tags"),
            "notable": notable,
        })
    out.sort(key=lambda x: x["dist_nm"])
    return out


# Real runway inventories (magnetic headings of each landing end) for the
# airports the board actually serves. Aircraft land and depart INTO the wind,
# so the active end is the one whose heading is most opposed to where the wind
# is coming from. We only ever name runways that physically exist — no more
# inventing a "Rwy 34" at an airport that has no such runway.
#
# Each entry: ICAO -> list of (end_label, magnetic_heading_degrees).
RUNWAYS = {
    # Phoenix Sky Harbor — three parallel east-west runways.
    "KPHX": [("07L", 75), ("25R", 255), ("07R", 75), ("25L", 255),
             ("08", 84), ("26", 264)],
    # Phoenix-Mesa Gateway — parallel east-west.
    "KIWA": [("12L", 122), ("30R", 302), ("12C", 122), ("30C", 302),
             ("12R", 122), ("30L", 302)],
    # Los Angeles — two east-west pairs.
    "KLAX": [("06L", 69), ("24R", 249), ("06R", 69), ("24L", 249),
             ("07L", 69), ("25R", 249), ("07R", 69), ("25L", 249)],
    "KLAS": [("01L", 8), ("19R", 188), ("01R", 8), ("19L", 188),
             ("08L", 82), ("26R", 262), ("08R", 82), ("26L", 262)],
    "KSAN": [("09", 90), ("27", 270)],
    "KSEA": [("16L", 160), ("34R", 340), ("16C", 160), ("34C", 340),
             ("16R", 160), ("34L", 340)],
    "KDEN": [("16L", 160), ("34R", 340), ("16R", 160), ("34L", 340),
             ("17L", 172), ("35R", 352), ("17R", 172), ("35L", 352),
             ("07", 82), ("25", 262), ("08", 82), ("26", 262)],
    "KDFW": [("17L", 175), ("35R", 355), ("17C", 175), ("35C", 355),
             ("17R", 175), ("35L", 355), ("18L", 175), ("36R", 355),
             ("18R", 175), ("36L", 355), ("13L", 130), ("31R", 310),
             ("13R", 130), ("31L", 310)],
    "KORD": [("10L", 100), ("28R", 280), ("10C", 100), ("28C", 280),
             ("10R", 100), ("28L", 280), ("09L", 92), ("27R", 272),
             ("09C", 92), ("27C", 272), ("09R", 92), ("27L", 272),
             ("04L", 42), ("22R", 222), ("04R", 42), ("22L", 222)],
    "KATL": [("08L", 92), ("26R", 272), ("08R", 92), ("26L", 272),
             ("09L", 92), ("27R", 272), ("09R", 92), ("27L", 272),
             ("10", 92), ("28", 272)],
    "KSFO": [("01L", 12), ("19R", 192), ("01R", 12), ("19L", 192),
             ("10L", 117), ("28R", 297), ("10R", 117), ("28L", 297)],
    "KJFK": [("04L", 43), ("22R", 223), ("04R", 43), ("22L", 223),
             ("13L", 133), ("31R", 313), ("13R", 133), ("31L", 313)],
    "KDAL": [("13L", 132), ("31R", 312), ("13R", 132), ("31L", 312),
             ("18", 175), ("36", 355)],
}


def _flow_words(hdg: int) -> str:
    """Plain-language travel direction for a runway heading (which way the
    aircraft are pointed as they land / take off)."""
    dirs = [(0, "north"), (45, "northeast"), (90, "east"), (135, "southeast"),
            (180, "south"), (225, "southwest"), (270, "west"), (315, "northwest"),
            (360, "north")]
    return min(dirs, key=lambda d: abs(d[0] - hdg))[1]


def _active_runway(icao, wind_dir):
    """Return (runway_label, flow_phrase) using the airport's REAL runways and
    the current wind, or (None, None) when we have no runway data or no wind.

    Aircraft land/depart into the wind, so we pick the runway end whose heading
    is most opposed to the wind's source direction (max head-wind component)."""
    import math
    rwys = RUNWAYS.get((icao or "").upper())
    if not rwys or wind_dir is None:
        return None, None
    try:
        w = float(wind_dir)
    except (ValueError, TypeError):
        return None, None
    # head-wind component for an end of heading h with wind FROM direction w:
    # strongest when the aircraft points toward w, i.e. cos(h - w) is largest.
    best = max(rwys, key=lambda r: math.cos(math.radians(r[1] - w)))
    label, hdg = best
    # group parallel ends that share a heading, e.g. "25L/25R/26"
    same = [lb for (lb, h) in rwys if abs(h - hdg) <= 15]
    grp = "/".join(dict.fromkeys(same))  # dedupe, keep order
    return grp, _flow_words(hdg)


def airport_weather(icao: str) -> dict:
    """Current METAR-derived weather + likely active runway for an airport.

    Free from aviationweather.gov (US + many intl). Returns a compact summary."""
    url = config.METAR_BASE
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(url, params={"ids": icao.upper(), "format": "json"})
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "note": "weather unavailable"}
    if not data:
        return {"ok": False, "note": "no report for this airport"}
    m = data[0] if isinstance(data, list) else data
    wdir = m.get("wdir")
    wind_dir = wdir if isinstance(wdir, (int, float)) else None
    rwy, flow = _active_runway(icao, wind_dir)
    return {
        "ok": True,
        "airport": icao.upper(),
        "raw": m.get("rawOb"),
        "temp_c": m.get("temp"),
        "wind_dir": wind_dir,
        "wind_kt": m.get("wspd"),
        "wind_gust_kt": m.get("wgst"),
        "visibility": m.get("visib"),
        "clouds": [c.get("cover") for c in (m.get("clouds") or []) if c.get("cover")],
        "active_runway": rwy,       # real runway end(s), only for known airports
        "runway_flow": flow,        # plain-language: "west", "east", etc.
        "flight_category": m.get("fltCat"),  # VFR/MVFR/IFR/LIFR
        "delay": airport_status(icao),  # FAA ground stop / delay chip (US only)
    }


# ---- FAA airport operational status (ground stops / delays) ----------------
# One nationwide feed (free, no key). We cache it briefly so a busy board doesn't
# hammer the FAA. US airports only — the feed uses IATA codes (PHX, not KPHX).
_FAA_URL = "https://nasstatus.faa.gov/api/airport-events"
_FAA_CACHE = {"at": 0, "by_iata": None}


def _iata_of(icao: str):
    """IATA code for airports the FAA ASWS feed covers (US only). Returns None
    for everything else so the delay chip hides instead of implying coverage.

    For contiguous-US K-airports the IATA is the ICAO minus the leading 'K'
    (KPHX->PHX). Alaska/Hawaii/territories (P*, T*) have no clean strip rule, so
    a small map handles the busy ones and the rest fall through to None."""
    icao = (icao or "").upper().strip()
    if len(icao) == 4 and icao[0] == "K":
        return icao[1:]
    return _NONK_US.get(icao)   # HNL, ANC, etc.


# Non-K US airports the FAA covers, ICAO -> IATA (extend as needed).
_NONK_US = {
    "PHNL": "HNL", "PHOG": "OGG", "PHKO": "KOA", "PHTO": "ITO", "PHLI": "LIH",
    "PANC": "ANC", "PAFA": "FAI", "PAJN": "JNU", "TJSJ": "SJU", "PGUM": "GUM",
}


def _faa_events():
    """All current FAA airport events, keyed by IATA. Cached ~90s."""
    import time
    now = time.time()
    if _FAA_CACHE["by_iata"] is not None and now - _FAA_CACHE["at"] < 90:
        return _FAA_CACHE["by_iata"]
    by = {}
    try:
        with httpx.Client(timeout=12) as c:
            r = c.get(_FAA_URL, headers={"Accept": "application/json"})
            r.raise_for_status()
            data = r.json()
        for ev in (data or []):
            aid = (ev.get("airportId") or "").upper()
            if aid:
                by[aid] = ev
    except (httpx.HTTPError, ValueError, TypeError):
        by = _FAA_CACHE["by_iata"] or {}   # keep last good on a blip
    _FAA_CACHE["by_iata"] = by
    _FAA_CACHE["at"] = now
    return by


def airport_status(icao: str) -> dict:
    """Compact operational status for the weather strip's delay chip.

    Returns {ok, level, label, detail}. level is 'ontime' | 'minor' | 'major'.
    'ok' is False for airports the FAA doesn't cover (non-US) so the UI can hide
    the chip rather than imply everything is fine."""
    iata = _iata_of(icao)
    if not iata:
        return {"ok": False}
    ev = _faa_events().get(iata)
    # Absent from the feed => FAA has no active program for it => running normally
    if ev is None:
        return {"ok": True, "level": "ontime", "label": "On time", "detail": None}

    gs = ev.get("groundStop") or None
    gd = ev.get("groundDelay") or None
    clo = ev.get("airportClosure") or None
    arr = ev.get("arrivalDelay") or None
    dep = ev.get("departureDelay") or None

    def _avg(d):
        v = (d or {}).get("avgDelay") or (d or {}).get("averageDelay")
        return str(v).strip() if v else None

    if clo:
        return {"ok": True, "level": "major", "label": "Airport closed",
                "detail": (clo.get("simpleText") or clo.get("freeForm") or "")[:120] or None}
    if gs:
        cond = gs.get("impactingCondition") or gs.get("reason")
        return {"ok": True, "level": "major", "label": "Ground stop",
                "detail": (cond or "")[:120] or None}
    if gd:
        avg = _avg(gd)
        cond = gd.get("impactingCondition") or gd.get("reason")
        lab = f"Ground delay{(' ~' + avg) if avg else ''}"
        return {"ok": True, "level": "major", "label": lab,
                "detail": (cond or "")[:120] or None}
    if dep or arr:
        which = "Departures" if dep else "Arrivals"
        d = dep or arr
        avg = _avg(d)
        cond = (d or {}).get("reason")
        lab = f"{which} delayed{(' ~' + avg) if avg else ''}"
        return {"ok": True, "level": "minor", "label": lab,
                "detail": (cond or "")[:120] or None}
    return {"ok": True, "level": "ontime", "label": "On time", "detail": None}
