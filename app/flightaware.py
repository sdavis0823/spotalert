"""FlightAware AeroAPI — pre-takeoff / scheduled-arrival alerts.

This is the piece that lets SpotAlert (like JetTip) alert you BEFORE a plane
takes off. FlightAware sees flights in a "scheduled" state — with the tail
number and destination — once the schedule/flight plan is loaded, which is often
the day before (airlines) or hours before (filed GA/military). The free ADS-B
feeds can't do this: they only see aircraft once they're transmitting.

We query scheduled arrivals for a covered airport, then keep the ones whose tail
is a notable airframe (special livery / warbird / military / watched, etc.).

Gated on FLIGHTAWARE_API_KEY. Without it, this is a no-op and the app runs on
the free live layer only. Uses the cheapest useful call:
    GET /airports/{id}/flights/scheduled_arrivals
"""
import time
import collections
import httpx
from . import config, aircraft as ac_mod


# Last AeroAPI error seen (for the /usage diagnostic). None when healthy.
_LAST_ERROR = None

# Sliding-window rate limiter — the free tier allows only ~5 queries/minute.
_CALL_TIMES = collections.deque()


def _rate_wait():
    """Block until making another AeroAPI call stays within the per-minute cap."""
    win = 60.0
    now = time.time()
    while _CALL_TIMES and now - _CALL_TIMES[0] > win:
        _CALL_TIMES.popleft()
    if len(_CALL_TIMES) >= max(1, config.FA_RATE_PER_MIN):
        sleep_for = win - (now - _CALL_TIMES[0]) + 0.5
        if sleep_for > 0:
            time.sleep(min(sleep_for, 65))
        now = time.time()
        while _CALL_TIMES and now - _CALL_TIMES[0] > win:
            _CALL_TIMES.popleft()
    _CALL_TIMES.append(time.time())


def last_error():
    return _LAST_ERROR


# ---- monthly query budget (hard guard against paid overage) --------------
def _month_key() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def usage_this_month() -> int:
    from . import db
    with db.get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS api_usage "
                     "(month TEXT PRIMARY KEY, fa_queries INTEGER DEFAULT 0)")
        row = conn.execute("SELECT fa_queries FROM api_usage WHERE month=?",
                           (_month_key(),)).fetchone()
    return int(row[0]) if row else 0


def budget_remaining() -> int:
    return max(0, config.FA_MONTHLY_QUERY_BUDGET - usage_this_month())


def _record_query(n: int = 1) -> None:
    from . import db
    with db.get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS api_usage "
                     "(month TEXT PRIMARY KEY, fa_queries INTEGER DEFAULT 0)")
        conn.execute("INSERT INTO api_usage (month, fa_queries) VALUES (?,?) "
                     "ON CONFLICT(month) DO UPDATE SET fa_queries = fa_queries + ?",
                     (_month_key(), n, n))
        conn.commit()


class FlightAwareSource:
    name = "flightaware"

    def available(self) -> bool:
        return bool(config.FLIGHTAWARE_API_KEY)

    def budget_ok(self) -> bool:
        return budget_remaining() > 0

    def scheduled_arrivals(self, airport_icao: str, max_pages: int = 1,
                           start: str | None = None, end: str | None = None) -> list[dict]:
        """Return upcoming scheduled arrivals for an airport (not yet departed).

        start/end are ISO-8601 UTC strings to widen the look-ahead window.
        Each page (~15 flights) counts as one AeroAPI query.
        """
        params = {"max_pages": max_pages}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        data = self._get(f"/airports/{airport_icao}/flights/scheduled_arrivals", params)
        if not data:
            return []
        return [_parse_flight(f) for f in data.get("scheduled_arrivals", [])]


    def _get(self, path: str, params: dict | None = None) -> dict | None:
        """Single authenticated GET. Returns parsed JSON dict or None on error."""
        if not self.available():
            return None
        if not self.budget_ok():
            return None  # hard stop — monthly free-tier budget exhausted
        global _LAST_ERROR
        _rate_wait()  # respect the free tier's per-minute rate limit
        headers = {"x-apikey": config.FLIGHTAWARE_API_KEY, "Accept": "application/json"}
        url = f"{config.FLIGHTAWARE_BASE}{path}"
        try:
            with httpx.Client(timeout=25, headers=headers) as c:
                r = c.get(url, params=params or {})
                _record_query(1)  # count every call that actually hit AeroAPI
                if r.status_code >= 400:
                    _LAST_ERROR = f"HTTP {r.status_code}: {(r.text or '')[:180]}"
                    return None
                _LAST_ERROR = None
                return r.json()
        except (httpx.HTTPError, ValueError) as e:
            _LAST_ERROR = f"{type(e).__name__}: {str(e)[:180]}"
            return None

    def airport_flights(self, airport_icao: str) -> dict:
        """ONE combined call: scheduled + live arrivals & departures for an airport.

        Returns {scheduled_arrivals, scheduled_departures, arrivals, departures}
        each a list of parsed flight dicts. This is the thrifty core call — a
        single query yields pre-takeoff arrivals, upcoming departures, enroute
        ETAs, diversions and cancellations all at once.
        """
        data = self._get(f"/airports/{airport_icao}/flights") or {}
        out = {}
        for key in ("scheduled_arrivals", "scheduled_departures", "arrivals", "departures"):
            out[key] = [_parse_flight(f) for f in (data.get(key) or [])]
        return out

    def flight_search(self, query: str, max_pages: int = 1) -> list[dict]:
        """Global fleet search (query-hungry). AeroAPI query DSL, e.g.
        '-type A388' (all A380s airborne) or '-id[reg] N123'."""
        data = self._get("/flights/search", {"query": query, "max_pages": max_pages}) or {}
        return [_parse_flight(f) for f in (data.get("flights") or [])]

    def flight_by_ident(self, ident: str) -> dict | None:
        """Latest flight for a tail/ident — powers follow-a-tail-anywhere."""
        data = self._get(f"/flights/{ident}") or {}
        flights = [_parse_flight(f) for f in (data.get("flights") or [])]
        return flights[0] if flights else None


def _iso_to_epoch(s):
    if not s:
        return None
    try:
        # AeroAPI uses ISO 8601 UTC, e.g. 2026-08-24T14:20:00Z
        import calendar
        t = time.strptime(s.replace("Z", "UTC"), "%Y-%m-%dT%H:%M:%S%Z") if "Z" in s \
            else time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        return calendar.timegm(t)
    except (ValueError, TypeError):
        return None


def _code(d):
    d = d or {}
    return d.get("code") or d.get("code_icao") or d.get("code_iata")


def _parse_flight(f: dict) -> dict:
    return {
        "ident": (f.get("ident") or "").strip() or None,
        "registration": (f.get("registration") or "").strip() or None,
        "type": (f.get("aircraft_type") or "").strip() or None,  # AeroAPI pads with spaces
        "origin": _code(f.get("origin")),
        "destination": _code(f.get("destination")),
        # arrival timing
        "scheduled_on": _iso_to_epoch(f.get("scheduled_in") or f.get("scheduled_on")),
        "estimated_on": _iso_to_epoch(f.get("estimated_in") or f.get("estimated_on")),
        "actual_on": _iso_to_epoch(f.get("actual_in") or f.get("actual_on")),
        # departure timing
        "scheduled_off": _iso_to_epoch(f.get("scheduled_out") or f.get("scheduled_off")),
        "estimated_off": _iso_to_epoch(f.get("estimated_out") or f.get("estimated_off")),
        "actual_off": _iso_to_epoch(f.get("actual_out") or f.get("actual_off")),
        # state flags
        "diverted": bool(f.get("diverted")),
        "cancelled": bool(f.get("cancelled")),
        "status": f.get("status"),
        "progress_percent": f.get("progress_percent"),
    }


# Compact ICAO airline-code -> display name map so ordinary board rows read like
# JetTip ("AMERICAN AIRLINES", "SOUTHWEST") instead of a bare type code.
AIRLINE_ICAO = {
    "AAL": "American Airlines", "SWA": "Southwest", "DAL": "Delta", "UAL": "United",
    "ASA": "Alaska", "JBU": "JetBlue", "NKS": "Spirit", "FFT": "Frontier",
    "HAL": "Hawaiian", "SCX": "Sun Country", "AAY": "Allegiant", "QXE": "Horizon",
    "SKW": "SkyWest", "ENY": "Envoy", "RPA": "Republic", "EDV": "Endeavor",
    "FDX": "FedEx", "UPS": "UPS", "GTI": "Atlas Air", "ABX": "ABX Air",
    "ACA": "Air Canada", "WJA": "WestJet", "AMX": "Aeroméxico", "VOI": "Volaris",
    "BAW": "British Airways", "DLH": "Lufthansa", "AFR": "Air France", "KLM": "KLM",
    "UAE": "Emirates", "QTR": "Qatar Airways", "ANA": "ANA", "JAL": "JAL",
    "CPA": "Cathay Pacific", "SIA": "Singapore Airlines", "QFA": "Qantas",
    "ACA": "Air Canada", "AMX": "Aeroméxico", "CES": "China Eastern",
    "CSN": "China Southern", "CCA": "Air China", "AIC": "Air India",
}


def _airline_from_ident(ident):
    if not ident:
        return None
    return AIRLINE_ICAO.get(ident[:3].upper())


def _store_scheduled_board(conn, airport_icao, arrivals, departures, now, horizon):
    """Cache EVERY scheduled arrival & departure (not just notable) so the board
    can show the full JetTip-style list. Same data the scan already fetched — no
    extra AeroAPI queries. Replaces the airport's rows each scan."""
    conn.execute("DELETE FROM scheduled_flights WHERE airport_icao=?", (airport_icao,))
    rows = []

    def _row(fl, direction, when):
        if not when or when < now - 1800 or when > horizon:
            return None
        ac = _notable_for_flight(fl)
        reg = fl.get("registration")
        ident = fl.get("ident")
        notable = 1 if ac and ac.get("icao24") else 0
        operator = (ac.get("operator") if ac else None) or _airline_from_ident(ident)
        icao24 = (ac.get("icao24") if ac else None) or (reg or ident or "").lower() or None
        cat = ac.get("category") if ac else None
        tags = ",".join(ac.get("interest_tags") or []) if ac else None
        return (airport_icao, direction, ident, reg, fl.get("type"),
                fl.get("origin"), fl.get("destination"), int(when), operator,
                icao24, notable, cat, tags, fl.get("status"), now)

    for fl in arrivals:
        r = _row(fl, "arrival", fl.get("estimated_on") or fl.get("scheduled_on"))
        if r:
            rows.append(r)
    for fl in departures:
        r = _row(fl, "departure", fl.get("estimated_off") or fl.get("scheduled_off"))
        if r:
            rows.append(r)
    if rows:
        conn.executemany(
            """INSERT OR IGNORE INTO scheduled_flights
               (airport_icao,direction,ident,registration,type,origin,destination,
                event_time,operator,icao24,notable,category,tags,status,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    return len(rows)


def notable_scheduled(airport_icao: str) -> list[dict]:
    """Scheduled arrivals filtered to notable airframes, with lead-time info.

    Cross-references the tail against our curated knowledge base: a flight is
    notable if its registration is a base-interest frame (special livery,
    warbird, tanker, testbed, military, gov, large private) in the aircraft DB.
    """
    src = FlightAwareSource()
    if not src.available():
        return []
    now = int(time.time())
    horizon = now + config.SCHEDULED_LOOKAHEAD_HOURS * 3600
    hits = []
    for fl in src.scheduled_arrivals(airport_icao):
        reg = (fl.get("registration") or "").upper()
        if not reg:
            continue
        sched = fl.get("estimated_on") or fl.get("scheduled_on")
        if not sched or sched < now - 900 or sched > horizon:
            continue
        ac = _lookup_by_reg(reg)
        if not ac or not ac.get("base_interest"):
            continue
        ac = ac_mod.classify(ac)
        hits.append({
            "airport_icao": airport_icao,
            "registration": reg,
            "icao24": ac.get("icao24"),
            "ident": fl.get("ident"),
            "origin": fl.get("origin"),
            "scheduled_on": sched,
            "lead_minutes": max(0, (sched - now) // 60),
            "display": ac_mod.display_name(ac),
            "category": ac.get("category"),
            "interest_tags": ac.get("interest_tags"),
        })
    hits.sort(key=lambda h: h["scheduled_on"])
    return hits


def _lookup_by_reg(reg: str) -> dict | None:
    from . import db
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM aircraft WHERE UPPER(registration)=? LIMIT 1", (reg.upper(),)).fetchone()
    return dict(row) if row else None


# Inherently interesting airframe types even when the specific tail isn't
# curated — quads, big widebodies, and rare/vintage frames spotters chase.
RARE_TYPES = {
    "A388", "A380", "B748", "B744", "B742", "B741", "BLCF", "A346", "A345",
    "A343", "A342", "MD11", "B762", "AN124", "A124", "AN225", "A225", "IL76",
    "IL96", "C5M", "C5", "B703", "CONC", "DC10", "MD87", "B77L", "B74S", "B74R",
}
NOTABLE_CATS = ("military", "gov", "warbird", "tanker", "testbed", "special")

# Genuine business-jet ICAO types (inherently notable) — deliberately EXCLUDES
# airliner types like B737/A320 that the generic classifier would over-flag.
BIZJET_TYPES = {
    "GLF2", "GLF3", "GLF4", "GLF5", "GLF6", "GL5T", "GL6T", "GL7T", "GLEX",
    "G650", "G280", "GALX", "C68A", "C700", "C750", "C56X", "C560", "C680",
    "C525", "C25A", "C25B", "C25C", "C510", "FA7X", "FA8X", "FA6X", "F900",
    "F2TH", "FA50", "CL60", "CL30", "CL35", "E55P", "E50P", "E545", "E550",
    "PC24", "HDJT", "LJ60", "LJ75", "LJ45", "LJ35", "H25B", "H25C", "PRM1",
    "BE40", "BBJ", "GA5C", "GA6C", "GA7C",
}


def _notable_for_flight(fl: dict) -> dict | None:
    """Notable airframe for a scheduled flight — by curated tail OR by type.

    1) If the registration is a curated base-interest frame (special livery,
       warbird, watched, etc.) use that.
    2) Otherwise flag inherently-notable *types*: military / warbird / large
       private (via the classifier's heuristics) or a rare widebody/quad.
    Returns a classified aircraft dict with a usable icao24 key, else None.
    """
    from . import liveries
    from . import notable_registry as nreg
    reg = (fl.get("registration") or "").upper()
    tc = (fl.get("type") or "").upper()
    ident = (fl.get("ident") or "").upper()
    # 0) community notable-aircraft registry (~17k tails: military, gov, historic,
    #    firefighters, testbeds, celebrity/distinctive) — matched by registration.
    if reg:
        info = nreg.by_reg(reg)
        if info:
            return {"icao24": (info.get("h") or reg).lower(), "registration": reg,
                    "typecode": tc or info.get("t"), "model": None,
                    "operator": info.get("o"), "category": info.get("c") or "special",
                    "interest_tags": info.get("g") or [], "base_interest": 1,
                    "is_blocked": info.get("c") in ("military", "gov")}
    # sports/team & pro charters — matched by known charter-operator callsign
    # prefixes. STARTER set; grow it as you spot more.
    CHARTER_ICAO = ("OAE", "SWQ", "MMZ", "RYW", "EGF", "CKS", "GXA", "VTE", "SNC")
    if ident[:3] in CHARTER_ICAO:
        return {"icao24": (reg or ident).lower(), "registration": reg or None,
                "typecode": tc or None, "model": None, "operator": None,
                "category": "charter", "interest_tags": ["charter"],
                "base_interest": 1, "is_blocked": False}
    # 1a) curated special livery (works without the identity DB) — any tail we
    #     know wears a special/heritage scheme, matched purely by registration.
    if reg:
        lv = liveries.lookup(reg)
        if lv:
            category, tags = lv
            return {"icao24": reg.lower(), "registration": reg, "typecode": tc or None,
                    "model": None, "operator": None, "category": category,
                    "interest_tags": tags, "base_interest": 1, "is_blocked": False}
    # 1b) curated tail already in the identity DB (watched / base-interest)
    if reg:
        ac = _lookup_by_reg(reg)
        if ac and ac.get("base_interest"):
            ac = ac_mod.classify(ac)
            if ac.get("icao24"):
                return ac
    # 2) inherently-notable type
    if tc:
        guess = ac_mod.classify({"icao24": "", "registration": reg or None,
                                 "typecode": tc, "model": None, "operator": None,
                                 "category": "unknown", "interest_tags": "",
                                 "base_interest": 0})
        is_rare = tc in RARE_TYPES
        is_biz = tc in BIZJET_TYPES
        # only military/warbird/etc (real heuristic types), rare widebodies, or
        # genuine bizjets — NOT ordinary airliner types.
        is_mil = guess.get("category") in NOTABLE_CATS
        if is_mil or is_rare or is_biz:
            key = (reg or fl.get("ident") or tc).lower()
            guess["icao24"] = key
            guess["registration"] = reg or None
            if is_rare and guess.get("category") in ("unknown", "airliner", None):
                guess["interest_tags"] = ["widebody-rare"]
                guess["category"] = "special"
            elif is_biz and guess.get("category") in ("unknown", None):
                guess["interest_tags"] = ["large-private"]
                guess["category"] = "private"
            return guess
    return None


def _emit_alert(conn, airport_icao, icao24, direction, ident, event_time,
                reason, eta_minutes=None, dedup_secs=3600) -> bool:
    """Insert an alert deduped by (airport, icao24, direction, recent window)."""
    now = int(time.time())
    exists = conn.execute(
        """SELECT 1 FROM alerts WHERE airport_icao=? AND icao24=? AND direction=?
           AND event_time > ? LIMIT 1""",
        (airport_icao, icao24, direction, now - dedup_secs)).fetchone()
    if exists:
        return False
    conn.execute(
        """INSERT OR IGNORE INTO alerts
           (airport_icao, icao24, direction, callsign, event_time, priority,
            reason, visit_count, created_at, eta_minutes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (airport_icao, icao24, direction, ident, int(event_time), "red",
         reason, 0, now, eta_minutes))
    return True


def scan_airport_flights(airport_icao: str) -> dict:
    """Thrifty combined scan: ONE AeroAPI call -> pre-takeoff, departures,
    enroute ETAs, diversions and cancellations, for notable airframes only."""
    from . import db
    src = FlightAwareSource()
    if not src.available():
        return {"candidates": 0, "new_alerts": 0, "detail": []}
    flights = src.airport_flights(airport_icao)
    now = int(time.time())
    horizon = now + config.SCHEDULED_LOOKAHEAD_HOURS * 3600
    created = 0
    kinds = {"scheduled": 0, "enroute": 0, "departing": 0, "diversion": 0, "cancelled": 0}

    # Wider look-ahead: page through scheduled arrivals across the horizon so we
    # catch notable frames hours ahead, not just the immediate ~15 flights.
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(horizon))
    wide = src.scheduled_arrivals(airport_icao, max_pages=config.FA_SCHED_PAGES,
                                  start=start, end=end)
    seen_idents = set()
    arrivals_all = []
    for fl in wide + flights.get("scheduled_arrivals", []) + flights.get("arrivals", []):
        k = fl.get("ident") or str(id(fl))
        if k in seen_idents:
            continue
        seen_idents.add(k)
        arrivals_all.append(fl)

    departures_all = flights.get("scheduled_departures", []) + flights.get("departures", [])

    with db.get_conn() as conn:
        # Cache the FULL board (every arrival & departure, notable or not) so the
        # UI can show everything coming in like JetTip. No extra AeroAPI queries.
        try:
            _store_scheduled_board(conn, airport_icao, arrivals_all, departures_all,
                                   now, horizon)
        except Exception:  # noqa: BLE001 — board cache is best-effort
            pass

        # --- arrivals side: pre-takeoff, enroute ETA, diversion, cancelled ---
        for fl in arrivals_all:
            ac = _notable_for_flight(fl)
            if not ac or not ac.get("icao24"):
                continue
            hex_ = ac["icao24"]
            arr = fl.get("estimated_on") or fl.get("scheduled_on")
            name = ac_mod.display_name(ac)
            if fl.get("cancelled"):
                if _emit_alert(conn, airport_icao, hex_, "cancelled", fl.get("ident"),
                               arr or now, f"CANCELLED — {name}: {fl.get('origin') or '?'} → "
                               f"{airport_icao} flight cancelled"):
                    created += 1; kinds["cancelled"] += 1
                continue
            if fl.get("diverted"):
                if _emit_alert(conn, airport_icao, hex_, "diversion", fl.get("ident"),
                               arr or now, f"DIVERSION — {name} diverting to {airport_icao} "
                               f"(was {fl.get('destination') or 'elsewhere'})"):
                    created += 1; kinds["diversion"] += 1
                continue
            if not arr or arr < now - 900 or arr > horizon:
                continue
            if fl.get("actual_off"):
                # airborne & inbound -> real FlightAware ETA
                eta = max(0, (arr - now) // 60)
                if _emit_alert(conn, airport_icao, hex_, "enroute", fl.get("ident"), arr,
                               f"ENROUTE — {fl.get('origin') or '?'} → {airport_icao}, "
                               f"arriving ~{eta} min (FlightAware ETA) · {name}",
                               eta_minutes=eta):
                    created += 1; kinds["enroute"] += 1
            else:
                # not yet departed -> pre-takeoff heads-up
                lead = max(0, (arr - now) // 60)
                lh, lm = divmod(int(lead), 60)
                when = "today" if lh < 18 else "tomorrow"
                if _emit_alert(conn, airport_icao, hex_, "scheduled", fl.get("ident"), arr,
                               f"SCHEDULED — {fl.get('origin') or '?'} → {airport_icao}, "
                               f"arriving {when} (~{lh}h{lm:02d}m out) · heads-up before it departs · {name}",
                               eta_minutes=int(lead)):
                    created += 1; kinds["scheduled"] += 1

        # --- departures side: notable jet about to LEAVE this airport ---
        for fl in departures_all:
            ac = _notable_for_flight(fl)
            if not ac or not ac.get("icao24") or fl.get("actual_off"):
                continue
            dep = fl.get("estimated_off") or fl.get("scheduled_off")
            if not dep or dep < now - 900 or dep > horizon:
                continue
            lead = max(0, (dep - now) // 60)
            if _emit_alert(conn, airport_icao, ac["icao24"], "departing", fl.get("ident"), dep,
                           f"DEPARTING — {ac_mod.display_name(ac)} leaving {airport_icao} → "
                           f"{fl.get('destination') or '?'} in ~{lead} min",
                           eta_minutes=int(lead)):
                created += 1; kinds["departing"] += 1

    return {"candidates": sum(len(v) for v in flights.values()),
            "new_alerts": created, "kinds": kinds}


def scan_and_alert(airport_icao: str) -> dict:
    """Backward-compatible entry — now runs the full combined scan."""
    return scan_airport_flights(airport_icao)


# ---- follow-a-tail-anywhere ---------------------------------------------
def _ensure_follows_table(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS follows "
                 "(ident TEXT PRIMARY KEY, added_at INTEGER)")


def add_follow(ident: str) -> None:
    from . import db
    ident = (ident or "").upper().strip()
    if not ident:
        return
    with db.get_conn() as conn:
        _ensure_follows_table(conn)
        conn.execute("INSERT OR IGNORE INTO follows (ident, added_at) VALUES (?,?)",
                     (ident, int(time.time())))
        conn.commit()


def remove_follow(ident: str) -> None:
    from . import db
    with db.get_conn() as conn:
        _ensure_follows_table(conn)
        conn.execute("DELETE FROM follows WHERE ident=?", ((ident or "").upper().strip(),))
        conn.commit()


def list_follows() -> list[str]:
    from . import db
    with db.get_conn() as conn:
        _ensure_follows_table(conn)
        return [r[0] for r in conn.execute("SELECT ident FROM follows ORDER BY ident").fetchall()]


def check_follows() -> dict:
    """Look up each followed tail's latest flight (1 query each), budget-guarded."""
    src = FlightAwareSource()
    out = []
    for ident in list_follows():
        if not src.budget_ok():
            break
        fl = src.flight_by_ident(ident)
        if fl:
            out.append({"ident": ident, "origin": fl.get("origin"),
                        "destination": fl.get("destination"), "status": fl.get("status"),
                        "type": fl.get("type"), "progress_percent": fl.get("progress_percent")})
        else:
            out.append({"ident": ident, "status": "no active flight"})
    return {"checked": len(out), "flights": out}
