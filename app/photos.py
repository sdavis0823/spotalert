"""Aircraft photos — the most recent picture of the *actual* airframe.

Spotters want the specific tail, not a generic type shot. We resolve a photo
through a provider chain, taking the first hit:

  1. Planespotters.net  — clean open API, by hex then registration.
  2. airport-data.com   — free thumbnail API (aggregates JetPhotos / Airliners
                          imagery), by Mode-S hex then registration.

(Note: JetPhotos has no open API and its terms forbid scraping, so it is reached
only indirectly via airport-data.com, which is a sanctioned aggregator.)

Every result carries attribution (photographer + link + which library) as the
providers require. Results — including "no photo found" — are cached in-process
with a TTL so board renders don't hammer the APIs.
"""
import time
import httpx
from . import aircraft as ac_mod

TTL = 12 * 3600
_cache: dict[str, tuple[float, dict | None]] = {}

PS_BASE = "https://api.planespotters.net/pub/photos"
AD_BASE = "https://airport-data.com/api/ac_thumb.json"


# Last diagnostic (for /api/photo/debug) — what the most recent PS call did.
LAST_DIAG = {}


# ---------------------------------------------------------------- providers
def _planespotters(client, icao24, registration):
    def fetch(url):
        r = client.get(url)
        LAST_DIAG["url"] = url
        LAST_DIAG["status"] = r.status_code
        LAST_DIAG["body"] = (r.text or "")[:200]
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            LAST_DIAG["parse_error"] = True
            return None

    def parse(payload):
        photos = (payload or {}).get("photos") or []
        if not photos:
            return None
        p = photos[0]
        return {
            "thumbnail": (p.get("thumbnail") or {}).get("src"),
            "thumbnail_large": (p.get("thumbnail_large") or {}).get("src"),
            "link": p.get("link"),
            "photographer": p.get("photographer"),
            "credit": "Planespotters.net",
            "source": "planespotters",
        }
    if icao24:
        r = parse(fetch(f"{PS_BASE}/hex/{icao24}"))
        if r:
            return r
    if registration:
        return parse(fetch(f"{PS_BASE}/reg/{registration}"))
    return None


def _airport_data(client, icao24, registration):
    def parse(payload):
        data = (payload or {}).get("data") or []
        if not data:
            return None
        p = data[0]
        img = p.get("image")
        return {
            "thumbnail": img,
            "thumbnail_large": img,
            "link": p.get("link"),
            "photographer": p.get("photographer"),
            "credit": "airport-data.com",
            "source": "airport-data",
        }
    if icao24:
        r = parse(client.get(AD_BASE, params={"m": icao24.upper(), "n": 1}).json())
        if r:
            return r
    if registration:
        return parse(client.get(AD_BASE, params={"r": registration, "n": 1}).json())
    return None


# Planespotters serves ~420px photos that fill the card cleanly. airport-data
# only serves 150px thumbnails that look blurry blown up, so it's not used for
# the card (a Planespotters miss falls back to AeroDataBox's image, then to a
# crisp type illustration via the frontend's resolution gate).
PROVIDERS = [_planespotters]


# ---------------------------------------------------------------- public API
def get_photo(icao24: str, registration: str | None = None) -> dict | None:
    """Most recent photo for an airframe, trying each provider in order.
    Caches the first hit (or None) so repeated lookups are cheap."""
    icao24 = (icao24 or "").lower().strip()
    if registration is None:
        registration = (ac_mod.get_aircraft(icao24) or {}).get("registration")

    key = f"hex:{icao24}|reg:{registration or ''}"
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]

    result = None
    _UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    with httpx.Client(timeout=12, follow_redirects=True,
                      headers={"User-Agent": _UA, "Accept": "application/json"}) as client:
        for provider in PROVIDERS:
            try:
                result = provider(client, icao24, registration)
            except (httpx.HTTPError, ValueError):
                result = None
            if result and result.get("thumbnail"):
                break

    _cache[key] = (time.time(), result)
    return result
