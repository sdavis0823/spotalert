"""Aircraft-type illustrations — smart fallback art + the visual Type Guide.

We shipped ~89 clean type illustrations (private jets, airliners, helicopters).
This module maps an aircraft's ICAO type code / model to the best-matching
illustration slug, used as the list thumbnail whenever a real photo is missing.
"""
import json
import os
import re

_DIR = os.path.join(os.path.dirname(__file__), "static", "types")
_MANIFEST = []
_SLUGS = set()


def manifest() -> list[dict]:
    global _MANIFEST, _SLUGS
    if _MANIFEST:
        return _MANIFEST
    try:
        with open(os.path.join(_DIR, "manifest.json")) as f:
            _MANIFEST = json.load(f)
        _SLUGS = {m["slug"] for m in _MANIFEST}
    except (OSError, ValueError):
        _MANIFEST = []
    return _MANIFEST


# Curated ICAO type-code -> illustration slug (nearest available render).
ICAO2SLUG = {
    "A223": "airbus-a220-300", "BCS3": "airbus-a220-300", "A220": "airbus-a220-300",
    "A319": "airbus-a319neo", "A19N": "airbus-a319neo",
    "A320": "airbus-a320neo", "A20N": "airbus-a320neo",
    "A321": "airbus-a321neo", "A21N": "airbus-a321neo",
    "A332": "airbus-a330-300", "A333": "airbus-a330-300",
    "A338": "airbus-a330-900neo", "A339": "airbus-a330-900neo",
    "A343": "airbus-a340-600", "A346": "airbus-a340-600",
    "A359": "airbus-a350-900", "A35K": "airbus-a350-1000",
    "A388": "airbus-a380-800",
    "B712": "boeing-717-200",
    "B737": "boeing-737-800", "B738": "boeing-737-800", "B739": "boeing-737-800",
    "B38M": "boeing-737-max-8", "B39M": "boeing-737-max-8", "BBJ": "boeing-737-800",
    "B744": "boeing-747-8", "B748": "boeing-747-8", "BLCF": "boeing-747-8",
    "B752": "boeing-757-200", "B753": "boeing-757-200",
    "B762": "boeing-767-300er", "B763": "boeing-767-300er", "B764": "boeing-767-300er",
    "B77L": "boeing-777-300er", "B77W": "boeing-777-300er", "B772": "boeing-777-300er",
    "B788": "boeing-787-9", "B789": "boeing-787-9", "B78X": "boeing-787-10",
    "E75L": "embraer-e175", "E75S": "embraer-e175", "E170": "embraer-e175",
    "E290": "embraer-e190-e2", "E29E": "embraer-e195-e2", "E195": "embraer-e195-e2",
    "CRJ9": "crj900", "CRJ7": "crj900", "CRJ2": "crj900",
    "AT76": "atr-72-600", "AT75": "atr-72-600", "DH8D": "dash-8-q400",
    "MD82": "md-80", "MD83": "md-80", "MD88": "md-80", "MD11": "md-11",
    # private jets
    "GLF4": "gulfstream-g450", "GLF5": "gulfstream-g550", "GLF6": "gulfstream-g650er",
    "GL5T": "gulfstream-g500", "GL6T": "gulfstream-g600", "GA5C": "gulfstream-g500",
    "G280": "gulfstream-g280", "GALX": "gulfstream-g280",
    "GL7T": "global-7500", "GLEX": "global-6000", "GL5000": "global-5000",
    "CL30": "challenger-3500", "CL35": "challenger-3500", "CL60": "challenger-650",
    "FA6X": "falcon-6x", "FA7X": "falcon-7x", "FA8X": "falcon-8x",
    "F900": "falcon-900lx", "F2TH": "falcon-2000lxs",
    "C68A": "citation-latitude", "C700": "citation-longitude",
    "C25A": "citation-cj2", "C25B": "citation-cj3", "C25C": "citation-cj4-gen2",
    "C510": "citation-m2-gen2", "C560": "citation-xls", "C56X": "citation-xls",
    "C680": "citation-sovereign", "C750": "citation-x", "C25M": "citation-m2-gen2",
    "E50P": "phenom-300e", "E55P": "phenom-300e", "PC24": "pilatus-pc-24",
    "HDJT": "hondajet-elite-ii",
    "LJ45": "learjet-45xr", "LJ60": "learjet-60xr", "LJ75": "learjet-75",
    "H25B": "hawker-800xp", "H25C": "hawker-900xp", "HA4T": "hawker-400xp",
    "E545": "praetor-500e", "E550": "praetor-600e",
    # helicopters
    "R44": "robinson-r44", "R66": "robinson-r66", "B06": "bell-206-jetranger",
    "B407": "bell-407", "B429": "bell-429", "B505": "bell-505-jet-ranger-x",
    "B412": "bell-412epx", "AS50": "airbus-h125", "H125": "airbus-h125",
    "EC45": "airbus-h145", "H145": "airbus-h145", "EC30": "airbus-h130",
    "EC35": "airbus-h135", "H135": "airbus-h135", "H160": "airbus-h160",
    "S76": "sikorsky-s-76", "S92": "sikorsky-s-92", "A139": "aw139",
    "A109": "leonardo-aw109", "A169": "leonardo-aw169",
    "H60": "uh-60-black-hawk", "UH60": "uh-60-black-hawk", "AH64": "ah-64-apache",
    "CH47": "ch-47-chinook", "H500": "md-500e",
}


# Precise human-model -> slug patterns, checked BEFORE the loose token match so
# shared number suffixes ("-300", "-900") can't cross-match the wrong type.
# First matching regex wins; nearest available render when exact art is absent.
MODEL_PATTERNS = [
    (r"a350[- ]?1000|a35k", "airbus-a350-1000"), (r"a350", "airbus-a350-900"),
    (r"a380", "airbus-a380-800"),
    (r"a330[- ]?900|a330neo|a33[89]", "airbus-a330-900neo"), (r"a330|a33[23]", "airbus-a330-300"),
    (r"a340", "airbus-a340-600"),
    (r"a220|bcs3|cs300", "airbus-a220-300"),
    (r"a321|a21n", "airbus-a321neo"),
    (r"a320|a20n", "airbus-a320neo"),
    (r"a319|a19n", "airbus-a319neo"),
    (r"787[- ]?10|78x", "boeing-787-10"), (r"787|dreamliner", "boeing-787-9"),
    (r"777", "boeing-777-300er"),
    (r"767", "boeing-767-300er"), (r"757", "boeing-757-200"),
    (r"747", "boeing-747-8"), (r"717", "boeing-717-200"),
    (r"737.*(max|8200)|73[89]m|max ?[89]", "boeing-737-max-8"), (r"73[0-9]|737", "boeing-737-800"),
    (r"e19[05]|e2 ?jet|embraer.*19[05]", "embraer-e195-e2"),
    (r"e17[05]|embraer.*17[05]|erj.?17", "embraer-e175"),
    (r"crj", "crj900"),
    (r"dash ?8|q400|dhc-?8", "dash-8-q400"),
    (r"atr[ -]?72|atr72", "atr-72-600"),
    (r"md[- ]?11", "md-11"), (r"md[- ]?8|md8", "md-80"),
]


def _model_slug(model: str | None):
    hay = re.sub(r"[^a-z0-9 ]", " ", (model or "").lower())
    for pat, slug in MODEL_PATTERNS:
        if slug in _SLUGS and re.search(pat, hay):
            return slug
    return None


def resolve(typecode: str | None, model: str | None = None) -> str | None:
    """Best illustration slug for an aircraft, or None if we have no art."""
    manifest()
    if not _SLUGS:
        return None
    tc = (typecode or "").upper().strip()
    if tc in ICAO2SLUG and ICAO2SLUG[tc] in _SLUGS:
        return ICAO2SLUG[tc]
    ms = _model_slug(model)
    if ms:
        return ms
    # token match against the human model, e.g. "Gulfstream G650" -> g650.
    # Ignore manufacturer-only words so military Boeings/Airbuses don't match
    # a random airliner; require a distinctive model token (a number, or a
    # model name >=4 chars like "apache"/"chinook").
    STOP = {"boeing", "airbus", "bombardier", "gulfstream", "cessna", "citation",
            "embraer", "dassault", "falcon", "mcdonnell", "douglas", "north",
            "american", "lockheed", "bae", "bell", "robinson", "sikorsky",
            "leonardo", "pilatus", "learjet", "hawker", "global", "challenger",
            "the", "series"}
    hay = re.sub(r"[^a-z0-9 ]", " ", (model or "").lower())
    toks = [t for t in hay.split() if len(t) >= 3 and t not in STOP]
    digit_toks = [t for t in toks if any(ch.isdigit() for ch in t)]
    # 1) a shared number token is a strong match
    for m in _MANIFEST:
        ntoks = set(re.sub(r"[^a-z0-9 ]", " ", m["name"].lower()).split())
        if any(t in ntoks for t in digit_toks):
            return m["slug"]
    # 2) else a distinctive alpha model word (>=4 chars)
    for m in _MANIFEST:
        ntoks = set(re.sub(r"[^a-z0-9 ]", " ", m["name"].lower()).split())
        if any(len(t) >= 4 and t in ntoks for t in toks):
            return m["slug"]
    return None
