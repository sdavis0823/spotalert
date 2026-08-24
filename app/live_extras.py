"""Free live-feed extras — no API key, no query budget.

Three spotter features built on data that costs nothing:
  * emergency squawk watch (7500/7600/7700) near a point,
  * "overhead now" — what's flying above your GPS location right now,
  * airport weather + likely active runway, from aviationweather.gov METAR.
"""
import httpx
from . import sources, config, aircraft as ac_mod

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
    }
