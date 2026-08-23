"""Pluggable flight-data source adapters.

Every adapter yields a common event shape so the rarity engine is
source-agnostic:

    {
        "airport_icao": "KSEA",
        "icao24":       "a1b2c3",
        "direction":    "arrival" | "departure",
        "callsign":     "ASA123" | None,
        "event_time":   1723948800,   # unix seconds
    }

Adapters implemented:
  * OpenSkySource     - free /flights/arrival & /flights/departure (the backbone;
                        structured airport boards + history, respects blocking).
  * AirplanesLiveSource - free unfiltered live snapshot near an airport; catches
                        the military / gov / blocked private jets JetTip hides.
                        Used to ENRICH the backbone with events OpenSky misses.

A source that lacks credentials / connectivity simply yields nothing, so the
app keeps running on seed data. FlightAware AeroAPI can be added as another
adapter with the same interface without touching the engine.
"""
import time
import math
import httpx
from . import config

# ----------------------------------------------------------------------------
# OpenSky (OAuth2 client-credentials) — free airport boards + history.
# ----------------------------------------------------------------------------
class OpenSkySource:
    name = "opensky"

    def __init__(self):
        self._token = None
        self._token_exp = 0

    def available(self) -> bool:
        # Anonymous access works too (limited), but airport endpoints need auth
        # for anything beyond the most recent window, so we require creds here.
        return bool(config.OPENSKY_CLIENT_ID and config.OPENSKY_CLIENT_SECRET)

    def _get_token(self, client: httpx.Client) -> str | None:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        if not self.available():
            return None
        resp = client.post(
            config.OPENSKY_TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": config.OPENSKY_CLIENT_ID,
                "client_secret": config.OPENSKY_CLIENT_SECRET,
            },
            timeout=20,
        )
        resp.raise_for_status()
        tok = resp.json()
        self._token = tok["access_token"]
        self._token_exp = time.time() + int(tok.get("expires_in", 1800))
        return self._token

    def fetch(self, airport_icao: str, begin: int, end: int):
        """Yield arrival + departure events for one airport over [begin, end].

        OpenSky caps each call to a 2-day interval, so we window it.
        """
        if not self.available():
            return
        with httpx.Client(base_url=config.OPENSKY_API_BASE) as client:
            token = self._get_token(client)
            if not token:
                return
            headers = {"Authorization": f"Bearer {token}"}
            step = 2 * 24 * 3600  # 2 days
            for direction, path in (("arrival", "/flights/arrival"),
                                    ("departure", "/flights/departure")):
                w_start = begin
                while w_start < end:
                    w_end = min(w_start + step, end)
                    try:
                        r = client.get(path, params={
                            "airport": airport_icao, "begin": w_start, "end": w_end,
                        }, headers=headers, timeout=30)
                        if r.status_code == 404:
                            w_start = w_end
                            continue
                        r.raise_for_status()
                        for f in r.json() or []:
                            ts = f.get("lastSeen") if direction == "arrival" else f.get("firstSeen")
                            yield {
                                "airport_icao": airport_icao,
                                "icao24": (f.get("icao24") or "").lower(),
                                "direction": direction,
                                "callsign": (f.get("callsign") or "").strip() or None,
                                "event_time": int(ts or w_end),
                            }
                    except (httpx.HTTPError, ValueError):
                        pass
                    w_start = w_end


# ----------------------------------------------------------------------------
# airplanes.live — free UNFILTERED live snapshot. Catches blocked/mil frames.
# ----------------------------------------------------------------------------
class AirplanesLiveSource:
    name = "airplanes.live"
    BASE = "https://api.airplanes.live/v2"

    def available(self) -> bool:
        return True  # public, no key for basic radius queries

    def fetch_snapshot(self, lat: float, lon: float, radius_nm: int = 30):
        """Yield currently-airborne/ground aircraft within radius of a point.

        These are live positions, not board events; the engine converts a
        low-altitude aircraft near the field into an arrival/departure candidate.
        """
        url = f"{self.BASE}/point/{lat}/{lon}/{min(radius_nm, 250)}"
        try:
            with httpx.Client() as client:
                r = client.get(url, timeout=20)
                r.raise_for_status()
                for ac in (r.json().get("ac") or []):
                    yield {
                        "icao24": (ac.get("hex") or "").lower().lstrip("~"),
                        "callsign": (ac.get("flight") or "").strip() or None,
                        "reg": ac.get("r"),
                        "type": ac.get("t"),
                        "alt": ac.get("alt_baro"),
                        "gs": ac.get("gs"),
                        "lat": ac.get("lat"),
                        "lon": ac.get("lon"),
                        "track": ac.get("track"),      # ground track (deg) — used by ETA heading filter
                        "squawk": ac.get("squawk"),    # transponder code — emergency detection
                        # airplanes.live marks military with 'dbFlags' bit 1
                        "military": bool((ac.get("dbFlags") or 0) & 1),
                    }
        except (httpx.HTTPError, ValueError):
            return


def haversine_nm(lat1, lon1, lat2, lon2) -> float:
    R = 3440.065  # nautical miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))
