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


def _runway_from_wind(wind_dir) -> str | None:
    """Approximate active runway: aircraft take off / land into the wind, so the
    runway number is the wind direction / 10 (rounded to nearest 10)."""
    if wind_dir is None:
        return None
    try:
        d = int(round(float(wind_dir) / 10.0)) % 36
        if d == 0:
            d = 36
        return f"{d:02d}"
    except (ValueError, TypeError):
        return None


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
    rwy = _runway_from_wind(wind_dir)
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
        "active_runway": rwy,
        "flight_category": m.get("fltCat"),  # VFR/MVFR/IFR/LIFR
    }
