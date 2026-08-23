"""World airport catalog — the searchable directory behind the airport picker.

Loads a static dataset of ~20k airports that carry a real ICAO or IATA code
(sourced from OurAirports, filtered to large/medium/small airports with codes),
each tagged with country, state/region, city and coordinates. Held in memory —
the file is small (~4 MB JSON) and read once at import.

The catalog is a *reference* only. Airports are copied into the `airports` DB
table on demand (when a user selects one) so the scheduler scans just the
airports people actually watch, not all 20k.
"""
import json
import os
import unicodedata

_CATALOG: list[dict] = []
_BY_ICAO: dict[str, dict] = {}
_PATH = os.path.join(os.path.dirname(__file__), "airports_catalog.json")


def _norm(s: str) -> str:
    """Lowercase + strip accents, for accent-insensitive search."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def _load() -> None:
    global _CATALOG, _BY_ICAO
    if _CATALOG:
        return
    try:
        with open(_PATH, encoding="utf-8") as f:
            _CATALOG = json.load(f)
    except (OSError, ValueError):
        _CATALOG = []
        return
    for a in _CATALOG:
        a["_s"] = _norm(" ".join(filter(None, [
            a.get("name"), a.get("city"), a.get("state"),
            a.get("country"), a.get("icao"), a.get("iata")])))
        _BY_ICAO[a["icao"].upper()] = a


def loaded() -> int:
    _load()
    return len(_CATALOG)


def get(icao: str) -> dict | None:
    _load()
    a = _BY_ICAO.get((icao or "").upper())
    if not a:
        return None
    return {k: v for k, v in a.items() if not k.startswith("_")}


def search(q: str, limit: int = 40) -> list[dict]:
    """Ranked search over name / city / state / country / ICAO / IATA."""
    _load()
    q = _norm(q).strip()
    if not q:
        return []
    toks = q.split()
    out = []
    for a in _CATALOG:
        hay = a["_s"]
        if not all(t in hay for t in toks):
            continue
        # rank: exact code match > code prefix > city/name prefix > substring;
        # nudge bigger airports and scheduled-service fields up.
        icao = a["icao"].lower()
        iata = (a.get("iata") or "").lower()
        score = 0
        if q == icao or q == iata:
            score = 100
        elif icao.startswith(q) or (iata and iata.startswith(q)):
            score = 80
        elif _norm(a.get("city") or "").startswith(q) or _norm(a["name"]).startswith(q):
            score = 60
        else:
            score = 30
        score += {"large": 12, "medium": 6, "small": 0}.get(a.get("type"), 0)
        if a.get("svc"):
            score += 4
        out.append((score, a))
    out.sort(key=lambda t: (-t[0], -({"large": 2, "medium": 1}.get(t[1].get("type"), 0)),
                            t[1]["name"]))
    return [_public(a) for _, a in out[:limit]]


def countries() -> list[dict]:
    """List of countries with airport counts, alphabetical."""
    _load()
    counts: dict[str, int] = {}
    cc: dict[str, str] = {}
    for a in _CATALOG:
        c = a.get("country") or "—"
        counts[c] = counts.get(c, 0) + 1
        cc[c] = a.get("cc") or ""
    return [{"country": c, "cc": cc[c], "count": counts[c]}
            for c in sorted(counts)]


def states(country: str) -> list[dict]:
    """States/regions within a country, with counts."""
    _load()
    counts: dict[str, int] = {}
    for a in _CATALOG:
        if a.get("country") != country:
            continue
        s = a.get("state") or "—"
        counts[s] = counts.get(s, 0) + 1
    return [{"state": s, "count": counts[s]} for s in sorted(counts)]


def airports_in(country: str, state: str | None = None) -> list[dict]:
    """Airports within a country (and optional state), city-sorted."""
    _load()
    rows = [a for a in _CATALOG if a.get("country") == country
            and (state is None or (a.get("state") or "—") == state)]
    rows.sort(key=lambda a: (a.get("city") or "", a["name"]))
    return [_public(a) for a in rows]


def _public(a: dict) -> dict:
    return {k: v for k, v in a.items() if not k.startswith("_")}
