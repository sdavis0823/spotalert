"""FastAPI application — SpotAlert (a JetTip clone).

Endpoints
  GET  /                         -> web UI
  GET  /api/airports             -> covered airports
  GET  /api/board/{icao}         -> arrival/departure board for one airport
  GET  /api/alerts               -> recent alerts (optionally ?airport=&priority=)
  GET  /api/aircraft/{icao24}    -> aircraft identity + interest
  POST /api/subscriptions        -> create/update a subscription (max airports enforced)
  GET  /api/subscriptions/{email}
  POST /api/refresh              -> pull live data (OpenSky + airplanes.live) and re-evaluate
"""
import time
import os
import re
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from . import db, config, aircraft as ac_mod, engine
from . import sources, notify, scheduler, eta as eta_mod, watchlist, logbook, photos, push, liveries
from . import flightaware as fa_mod
from . import aerodatabox as adb_mod
from . import airports_catalog as catalog
from . import live_extras
from . import type_art

app = FastAPI(title="SpotAlert — unusual-aircraft alerts")

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.on_event("startup")
def _startup():
    db.init_db()
    try:
        import seed
        if seed.bootstrap_reference():
            print("Bootstrapped reference data (airports + curated aircraft).")
    except Exception as e:  # noqa: BLE001
        print(f"reference bootstrap skipped: {e}")
    try:
        with db.get_conn() as conn:
            matched = liveries.apply(conn)
        if matched:
            print(f"Applied curated special liveries to {matched} known tails.")
    except Exception as e:  # noqa: BLE001
        print(f"livery apply skipped: {e}")
    scheduler.start()


@app.on_event("shutdown")
async def _shutdown():
    await scheduler.stop()


# ------------------------------------------------------------------ helpers
def _airport_row(conn, icao):
    r = conn.execute("SELECT * FROM airports WHERE icao=?", (icao.upper(),)).fetchone()
    return dict(r) if r else None


def _enrich_event_row(conn, row):
    ac = ac_mod.classify(ac_mod.get_aircraft(row["icao24"]))
    return {
        "icao24": row["icao24"],
        "callsign": row["callsign"],
        "direction": row["direction"],
        "event_time": row["event_time"],
        "registration": ac.get("registration"),
        "type": ac.get("typecode"),
        "model": ac.get("model"),
        "operator": ac.get("operator"),
        "category": ac.get("category"),
        "interest_tags": ac.get("interest_tags"),
        "is_blocked": ac.get("is_blocked"),
        "display": ac_mod.display_name(ac),
        "type_art": type_art.resolve(ac.get("typecode"), ac.get("model")),
    }


# route code (SEA, LAX, …) lives inside the FlightAware alert reason text, e.g.
# "SCHEDULED — SEA → KPHX, …" or "… KPHX → LAX in ~20 min". Pull the endpoint
# that ISN'T the tracked airport so the board's right column reads like JetTip.
_ROUTE_RE = re.compile(r"\b([A-Z]{3,4})\s*(?:→|-+>|>)\s*([A-Z]{3,4})\b")
_WAS_RE = re.compile(r"\(was\s+([A-Z]{3,4})\)")


def _iata_ish(code):
    """US ICAO codes are K+3 letters; JetTip shows the 3-letter form (KLAS->LAS)."""
    if code and len(code) == 4 and code[0] == "K" and code[1:].isalpha():
        return code[1:]
    return code


def _route_code_from_reason(reason, airport_icao):
    if not reason:
        return None
    ap = {airport_icao.upper(), airport_icao.upper().lstrip("K")}
    m = _ROUTE_RE.search(reason)
    if m:
        for code in m.groups():
            if code and code.upper() not in ap:
                return _iata_ish(code.upper())
        return _iata_ish(m.group(1).upper())
    w = _WAS_RE.search(reason)
    return _iata_ish(w.group(1).upper()) if w else None


def _scheduled_board_events(conn, icao, since, until):
    """The FULL board from the scheduled-flight cache for a time window — every
    arrival & departure, notable or not (JetTip's 'everything coming in'),
    INCLUDING flights earlier in the day (past), so a day tab shows the whole day
    midnight-to-midnight. Notable rows carry a category/tags for highlighting."""
    ap = {icao.upper(), icao.upper().lstrip("K")}

    def _code(c):
        return _iata_ish(c) if c else None

    rows = conn.execute(
        """SELECT * FROM scheduled_flights WHERE airport_icao=?
             AND event_time >= ? AND event_time < ?
           ORDER BY event_time ASC LIMIT 2500""",
        (icao, since, until)).fetchall()
    out = []
    for r in rows:
        # far endpoint: origin for arrivals, destination for departures
        far = r["origin"] if r["direction"] == "arrival" else r["destination"]
        if not far or far.upper() in ap:
            far = r["destination"] if r["direction"] == "arrival" else r["origin"]
        tags = [t for t in (r["tags"] or "").split(",") if t]
        direction = "scheduled" if r["direction"] == "arrival" else "departing"
        out.append({
            "icao24": r["icao24"],
            "callsign": r["ident"],
            "direction": direction,
            "event_time": r["event_time"],
            "registration": r["registration"],
            "type": r["type"],
            "model": None,
            "operator": r["operator"],
            "category": r["category"],
            "interest_tags": tags,
            "is_blocked": r["category"] in ("military", "gov"),
            "display": None,
            "type_art": type_art.resolve(r["type"], None),
            "priority": "red" if r["notable"] else None,
            "notable": bool(r["notable"]),
            "eta_minutes": None,
            "route_code": _code(far),
        })
    return out


# ------------------------------------------------------------------ routes
@app.get("/api/airports")
def list_airports():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM airports ORDER BY icao").fetchall()
    return [dict(r) for r in rows]


# ---- world airport catalog (search / browse / select) --------------------
@app.get("/api/airports/search")
def airports_search(q: str = Query(..., min_length=1), limit: int = Query(40, ge=1, le=100)):
    """Live search across the ~20k-airport catalog by name/city/state/code."""
    tracked = _tracked_set()
    out = catalog.search(q, limit)
    for a in out:
        a["tracked"] = a["icao"].upper() in tracked
    return {"count": len(out), "results": out}


@app.get("/api/airports/countries")
def airports_countries():
    return catalog.countries()


@app.get("/api/airports/states")
def airports_states(country: str = Query(...)):
    return catalog.states(country)


@app.get("/api/airports/browse")
def airports_browse(country: str = Query(...), state: str | None = None):
    tracked = _tracked_set()
    rows = catalog.airports_in(country, state)
    for a in rows:
        a["tracked"] = a["icao"].upper() in tracked
    return {"count": len(rows), "results": rows}


class SelectAirport(BaseModel):
    icao: str


@app.post("/api/airports/select")
def airports_select(body: SelectAirport):
    """Add a catalog airport to the tracked set so it gets scanned. Idempotent."""
    a = catalog.get(body.icao)
    if not a:
        raise HTTPException(404, f"Unknown airport: {body.icao}")
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO airports (icao, iata, name, city, lat, lon)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(icao) DO UPDATE SET
                 iata=excluded.iata, name=excluded.name, city=excluded.city,
                 lat=excluded.lat, lon=excluded.lon""",
            (a["icao"].upper(), a.get("iata"), a["name"], a.get("city"),
             a.get("lat"), a.get("lon")))
        conn.commit()
    return {"ok": True, "airport": a}


@app.post("/api/airports/remove")
def airports_remove(body: SelectAirport):
    """Stop tracking an airport (removes it and its alerts/visits)."""
    icao = body.icao.upper()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM airports WHERE icao=?", (icao,))
        conn.execute("DELETE FROM alerts WHERE airport_icao=?", (icao,))
        conn.execute("DELETE FROM visits WHERE airport_icao=?", (icao,))
        conn.commit()
    return {"ok": True, "removed": icao}


def _tracked_set() -> set[str]:
    with db.get_conn() as conn:
        return {r["icao"].upper() for r in conn.execute("SELECT icao FROM airports").fetchall()}


@app.get("/api/board/{icao}")
def board(icao: str, hours: int = Query(48, ge=1, le=720),
          direction: str | None = None, category: str | None = None,
          notable_only: bool = False,
          day_from: int | None = None, day_to: int | None = None):
    """When day_from/day_to (epoch seconds) are given, the board shows that
    whole calendar day — every flight from midnight to midnight, past included —
    which is what a day tab needs. Otherwise it falls back to the trailing
    `hours` window."""
    icao = icao.upper()
    now = int(time.time())
    win_from = day_from if day_from else now - hours * 3600
    win_to = day_to if day_to else now + 72 * 3600
    since = win_from
    with db.get_conn() as conn:
        if not _airport_row(conn, icao):
            raise HTTPException(404, f"Airport {icao} not covered")
        q = "SELECT * FROM visits WHERE airport_icao=? AND event_time>=? AND event_time<?"
        params = [icao, win_from, win_to]
        if direction in ("arrival", "departure"):
            q += " AND direction=?"
            params.append(direction)
        q += " ORDER BY event_time DESC LIMIT 500"
        rows = conn.execute(q, params).fetchall()
        # attach alert priority where present
        alerts = {(a["icao24"], a["direction"], a["event_time"]): a["priority"]
                  for a in conn.execute(
                      "SELECT icao24,direction,event_time,priority FROM alerts WHERE airport_icao=? AND event_time>=?",
                      (icao, since)).fetchall()}
        out = []
        for r in rows:
            e = _enrich_event_row(conn, r)
            e["priority"] = alerts.get((r["icao24"], r["direction"], r["event_time"]))
            if category and e["category"] != category:
                continue
            if notable_only and not (e["priority"] or e["is_blocked"]):
                continue
            e["route_code"] = None      # live ADS-B rows carry no route
            out.append(e)

        # merge scheduled rows for the same window (they carry the route code)
        seen = {(e["icao24"], e["direction"], e["event_time"]) for e in out}
        for se in _scheduled_board_events(conn, icao, win_from, win_to):
            if notable_only and not se.get("notable"):
                continue
            key = (se["icao24"], se["direction"], se["event_time"])
            if key in seen:
                continue
            if direction in ("arrival", "departure"):
                # respect an explicit arrivals/departures filter
                if direction == "arrival" and se["direction"] == "departing":
                    continue
                if direction == "departure" and se["direction"] != "departing":
                    continue
            seen.add(key)
            out.append(se)

    # JetTip-style order: upcoming flights (soonest first) on top, recent past below
    now = int(time.time())
    out.sort(key=lambda e: (e["event_time"] <= now, abs(e["event_time"] - now)))
    return {"airport": icao, "hours": hours, "count": len(out), "events": out}


@app.get("/api/alerts")
def alerts(airport: str | None = None, priority: str | None = None,
           limit: int = Query(100, ge=1, le=500)):
    with db.get_conn() as conn:
        q = "SELECT * FROM alerts WHERE 1=1"
        params = []
        if airport:
            q += " AND airport_icao=?"
            params.append(airport.upper())
        if priority in ("red", "blue"):
            q += " AND priority=?"
            params.append(priority)
        q += " ORDER BY event_time DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            ac = ac_mod.classify(ac_mod.get_aircraft(r["icao24"]))
            d = dict(r)
            d["display"] = ac_mod.display_name(ac)
            d["category"] = ac.get("category")
            d["interest_tags"] = ac.get("interest_tags")
            d["is_blocked"] = ac.get("is_blocked")
            out.append(d)
    return {"count": len(out), "alerts": out}


@app.get("/api/aircraft/{icao24}")
def aircraft_detail(icao24: str):
    ac = ac_mod.classify(ac_mod.get_aircraft(icao24))
    ac["display"] = ac_mod.display_name(ac)
    return ac


class TagIn(BaseModel):
    icao24: str | None = None
    registration: str | None = None
    tags: list[str] = []          # e.g. ["special-livery","arizona-one"]
    category: str = "special"     # special|warbird|tanker|testbed|military|gov|private
    base_interest: bool = True    # notable regardless of rarity -> always pushes


@app.post("/api/aircraft/tag")
def tag_aircraft(t: TagIn):
    """Curate a frame as notable so it always pushes — the way you grow the
    special-livery database without redeploying. Match by hex or registration."""
    if not t.icao24 and not t.registration:
        raise HTTPException(400, "provide icao24 or registration")
    tags = ",".join(s.strip().lower() for s in t.tags if s.strip())
    with db.get_conn() as conn:
        if t.icao24:
            row = conn.execute("SELECT icao24 FROM aircraft WHERE icao24=?", (t.icao24.lower(),)).fetchone()
        else:
            row = conn.execute("SELECT icao24 FROM aircraft WHERE UPPER(registration)=?",
                               (t.registration.upper(),)).fetchone()
        if row:
            conn.execute(
                """UPDATE aircraft SET category=?, interest_tags=?, base_interest=1,
                     registration=COALESCE(registration,?) WHERE icao24=?""",
                (t.category, tags, t.registration, row["icao24"]))
            hexid = row["icao24"]
        elif t.icao24:
            conn.execute(
                """INSERT INTO aircraft (icao24,registration,category,interest_tags,base_interest)
                   VALUES (?,?,?,?,1)""", (t.icao24.lower(), t.registration, t.category, tags))
            hexid = t.icao24.lower()
        else:
            raise HTTPException(404, "registration not found; provide icao24 to create it")
    return {"ok": True, "icao24": hexid, "category": t.category, "tags": tags.split(",") if tags else []}


@app.post("/api/liveries/apply")
def liveries_apply():
    """Re-apply the curated special-livery registry to known tails (run after
    loading a new aircraft identity DB)."""
    with db.get_conn() as conn:
        matched = liveries.apply(conn)
    return {"ok": True, "matched": matched, "curated": len(liveries.CURATED_LIVERIES)}


@app.get("/api/photo/{icao24}")
def aircraft_photo(icao24: str, reg: str | None = None):
    """Most recent photo of the actual airframe (Planespotters.net).
    Returns {} when no photo exists so the UI can show a graceful placeholder."""
    p = photos.get_photo(icao24, reg)
    return p or {}


class SubscriptionIn(BaseModel):
    email: EmailStr
    airports: list[str] = []
    want_red: bool = True
    want_blue: bool = True
    want_diversions: bool = False
    want_email: bool = True
    webhook_url: str = ""
    categories: list[str] = []


@app.post("/api/subscriptions")
def upsert_subscription(sub: SubscriptionIn):
    if len(sub.airports) > config.MAX_AIRPORTS_PER_SUB:
        raise HTTPException(400, f"Max {config.MAX_AIRPORTS_PER_SUB} airports per subscription")
    airports = [a.upper() for a in sub.airports][: config.MAX_AIRPORTS_PER_SUB]
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO subscriptions
                 (email,airports,want_red,want_blue,want_diversions,want_email,webhook_url,categories)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(email) DO UPDATE SET
                 airports=excluded.airports, want_red=excluded.want_red,
                 want_blue=excluded.want_blue, want_diversions=excluded.want_diversions,
                 want_email=excluded.want_email, webhook_url=excluded.webhook_url,
                 categories=excluded.categories""",
            (sub.email, ",".join(airports), int(sub.want_red), int(sub.want_blue),
             int(sub.want_diversions), int(sub.want_email), sub.webhook_url,
             ",".join(c.lower() for c in sub.categories)),
        )
    return {"ok": True, "email": sub.email, "airports": airports}


@app.get("/api/subscriptions/{email}")
def get_subscription(email: str):
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM subscriptions WHERE email=?", (email,)).fetchone()
    if not r:
        raise HTTPException(404, "No subscription")
    d = dict(r)
    d["airports"] = [a for a in (d.get("airports") or "").split(",") if a]
    d["categories"] = [c for c in (d.get("categories") or "").split(",") if c]
    return d


@app.get("/api/notifications")
def notifications(email: str | None = None, limit: int = Query(100, ge=1, le=500)):
    with db.get_conn() as conn:
        q = """SELECT n.*, a.airport_icao, a.icao24, a.priority, a.reason,
                      a.event_time AS ev_time, a.eta_minutes
               FROM notifications n JOIN alerts a ON a.id = n.alert_id"""
        params = []
        if email:
            q += " WHERE n.email = ?"
            params.append(email)
        q += " ORDER BY n.sent_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            ac = ac_mod.classify(ac_mod.get_aircraft(r["icao24"]))
            d = dict(r)
            d["display"] = ac_mod.display_name(ac)
            out.append(d)
    return {"count": len(out), "notifications": out}


@app.post("/api/notify/run")
def notify_run():
    """Manually dispatch notifications for any undelivered matching alerts."""
    return notify.dispatch_new()


@app.get("/api/scheduler")
def scheduler_status():
    return scheduler.STATE


# ---------------------------------------------------------------- live radar
@app.get("/api/live/{icao}")
def live_positions(icao: str, radius: int = Query(40, ge=5, le=120)):
    """Aircraft currently near an airport, for the radar scope. Each contact has
    bearing + distance from the field and a notable flag."""
    icao = icao.upper()
    with db.get_conn() as conn:
        ap = conn.execute("SELECT * FROM airports WHERE icao=?", (icao,)).fetchone()
        if not ap:
            raise HTTPException(404, "airport not covered")
        ap = dict(ap)
    alive = sources.AirplanesLiveSource()
    out = []
    for a in alive.fetch_snapshot(ap["lat"], ap["lon"], radius_nm=radius):
        if a.get("lat") is None or a.get("lon") is None:
            continue
        dist = sources.haversine_nm(ap["lat"], ap["lon"], a["lat"], a["lon"])
        if dist > radius:
            continue
        brg = eta_mod._bearing(ap["lat"], ap["lon"], a["lat"], a["lon"])
        ac = ac_mod.classify(ac_mod.get_aircraft(a["icao24"]))
        notable = bool(ac.get("base_interest") or a.get("military")
                       or ac.get("category") in ("military", "gov", "warbird", "tanker", "testbed", "special"))
        out.append({
            "icao24": a["icao24"], "callsign": a.get("callsign"),
            "registration": ac.get("registration"), "type": ac.get("typecode"),
            "display": ac_mod.display_name(ac) if ac.get("registration") else (a.get("callsign") or a["icao24"]),
            "bearing": round(brg, 1), "dist_nm": round(dist, 1),
            "alt": a.get("alt"), "gs": a.get("gs"), "track": a.get("track"),
            "notable": notable, "category": ac.get("category"),
            "is_blocked": ac.get("is_blocked"),
        })
    out.sort(key=lambda c: c["dist_nm"])
    return {"airport": icao, "radius": radius, "count": len(out), "contacts": out}


# ---------------------------------------------------------------- pre-takeoff
@app.get("/api/upcoming/{icao}")
def upcoming(icao: str):
    """Notable aircraft SCHEDULED into an airport (pre-takeoff). Reads stored
    scheduled alerts, plus a live FlightAware peek when a key is configured."""
    icao = icao.upper()
    now = int(time.time())
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM alerts WHERE airport_icao=? AND direction='scheduled'
               AND event_time > ? ORDER BY event_time ASC LIMIT 50""",
            (icao, now - 3600)).fetchall()
    out = []
    for r in rows:
        ac = ac_mod.classify(ac_mod.get_aircraft(r["icao24"]))
        d = dict(r)
        d["display"] = ac_mod.display_name(ac)
        d["category"] = ac.get("category")
        d["interest_tags"] = ac.get("interest_tags")
        d["lead_minutes"] = max(0, (r["event_time"] - now) // 60)
        out.append(d)
    return {"airport": icao, "count": len(out), "upcoming": out,
            "flightaware": fa_mod.FlightAwareSource().available()}


def _deep_scan_job(airports):
    """Background full-day sweep — paced by the rate limiter (a few minutes),
    then dispatch notifications for anything notable it turned up."""
    fa_mod.run_deep_scan(airports)
    try:
        notify.dispatch_new()
    except Exception:  # noqa: BLE001
        pass


@app.post("/api/flightaware/scan")
def flightaware_scan(background: BackgroundTasks, airport: str | None = None, deep: bool = False):
    """Manual 'Scan now'. Quick scan = a few pages of the next several hours,
    returned synchronously. Deep scan = a full next-day sweep that runs in the
    BACKGROUND over a few minutes (paced to the free tier's 5-queries/min limit)
    so no scheduled special livery is missed; poll /api/flightaware/scan/status."""
    src = fa_mod.FlightAwareSource()
    if not src.available():
        return {"ok": False, "note": "FlightAware key not set — add FLIGHTAWARE_API_KEY to enable pre-takeoff alerts."}
    if not src.budget_ok():
        return {"ok": False, "note": "Monthly free-tier query budget reached — scans resume next month (no charge).",
                "budget_remaining": 0}
    if airport:
        airports = [airport.upper()]
    else:
        with db.get_conn() as conn:
            airports = [r["icao"] for r in conn.execute("SELECT icao FROM airports").fetchall()]

    if deep:
        if fa_mod.deep_status().get("running"):
            return {"ok": True, "deep": True, "started": False, "already_running": True,
                    "note": "A deep scan is already running — it fills the board in over a few minutes.",
                    "budget_remaining": fa_mod.budget_remaining()}
        background.add_task(_deep_scan_job, airports)
        return {"ok": True, "deep": True, "started": True,
                "note": "Deep scan started — the full next day fills in over the next few minutes. "
                        "Leave this open; the board refreshes as flights arrive.",
                "budget_remaining": fa_mod.budget_remaining()}

    # quick synchronous scan
    total = 0
    scanned = 0
    for ap in airports:
        if not src.budget_ok():
            break
        total += fa_mod.scan_airport_flights(ap, pages=config.FA_SCHED_PAGES)["new_alerts"]
        scanned += 1
    notify.dispatch_new()
    return {"ok": True, "new_alerts": total, "airports_scanned": scanned,
            "deep": False, "pages": config.FA_SCHED_PAGES,
            "budget_remaining": fa_mod.budget_remaining()}


@app.get("/api/push/generate-keys")
def push_generate_keys():
    """One-time helper: make a stable VAPID keypair to paste into your host's
    env (VAPID_PUBLIC_KEY / VAPID_PRIVATE_KEY) so push survives restarts."""
    k = push.generate_pair()
    return {"ok": True, **k, "VAPID_SUBJECT": "mailto:alerts@spotalert.local",
            "how": "Add these three as environment variables on Render, then redeploy. "
                   "Keep the private key secret."}


@app.get("/api/liveries/digest")
def liveries_digest(airport: str = "KPHX", email: str = "spotter@example.com",
                    hours: int = 36, push_it: bool = True):
    """Morning digest: load the day's schedule (free) and push a summary of the
    special liveries coming into the airport in the next `hours`. Meant to be hit
    once each morning by a free external timer (which also wakes the server)."""
    icao = airport.upper()
    if adb_mod.available():
        try:
            adb_mod.load(icao, max(hours, 36))
        except Exception:  # noqa: BLE001
            pass
    now = int(time.time())
    with db.get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM scheduled_flights WHERE airport_icao=?
                 AND event_time > ? AND event_time < ? AND notable=1
               ORDER BY event_time ASC""",
            (icao, now, now + hours * 3600)).fetchall()
    seen, liveries_list = set(), []
    for r in rows:
        tags = (r["tags"] or "")
        is_livery = (r["category"] == "special") or bool(re.search(r"livery|retro|heritage", tags, re.I))
        if not is_livery:
            continue
        key = (r["registration"] or r["ident"] or "")
        if key in seen:
            continue
        seen.add(key)
        nm = next((t for t in tags.split(",") if t and t not in
                   ("special-livery", "special", "retro", "heritage")), None)
        pretty = (nm or "").replace("-", " ").title() or (r["operator"] or r["ident"] or "special")
        liveries_list.append(pretty)
    code = icao[1:] if len(icao) == 4 and icao[0] == "K" else icao
    if liveries_list:
        n = len(liveries_list)
        title = f"✦ {n} special liver{'y' if n == 1 else 'ies'} into {code}"
        body = ", ".join(liveries_list[:6]) + ("…" if n > 6 else "")
    else:
        title = f"No special liveries into {code} today"
        body = "Nothing special scheduled in the next day."
    result = {"ok": True, "airport": icao, "count": len(liveries_list),
              "liveries": liveries_list}
    if push_it:
        st, detail = push.send(email, title, body, url="/")
        result["push"] = {"status": st, "detail": detail, "title": title, "body": body}
    else:
        result["preview"] = {"title": title, "body": body}
    return result


@app.get("/api/flight/detail")
def flight_detail(ident: str):
    """Tap-a-plane detail card: aircraft photo, model, gate, live status, route."""
    if adb_mod.available():
        d = adb_mod.flight_detail(ident)
        if d:
            # Guarantee an image: if AeroDataBox has no real photo (common for
            # flights whose tail isn't assigned yet), fall back to a clean
            # illustration of the aircraft TYPE so every card shows a plane.
            slug = type_art.resolve(None, d.get("model"))
            d["type_art"] = slug
            # AeroDataBox's photo is the ACTUAL airframe only when a tail number
            # is known. Without a reg its "photo" can be any operator's example of
            # the type (e.g. a WOW Air jet for an American flight) — misleading.
            # So when there's no reg, show a labeled type illustration instead.
            reg = d.get("registration")
            # AeroDataBox's OWN photo (from adb_mod.flight_detail) — a real photo of
            # the actual airframe when a tail is known, and it's reachable from this
            # server. Keep it as a fallback before the illustration.
            adb_img = d.get("image_url")
            adb_credit = d.get("image_credit")
            adb_link = d.get("image_link")
            if not reg:
                # No tail: AeroDataBox's image can be any operator's example of the
                # type (a WOW Air jet for an American flight) — misleading. Use the
                # labeled illustration instead.
                d["image_url"] = f"/static/types/{slug}.png" if slug else None
                d["image_credit"] = None
                d["image_link"] = None
                d["image_is_art"] = bool(slug)
            else:
                # Real photo of THIS airframe. 1st choice Planespotters (latest by
                # date taken, if reachable); else AeroDataBox's own photo (real
                # airframe, works from this server); else the labeled illustration.
                pic = None
                try:
                    pic = photos.get_photo(d.get("icao24") or "", reg)
                except Exception:  # noqa: BLE001
                    pic = None
                if pic and (pic.get("thumbnail_large") or pic.get("thumbnail")):
                    d["image_url"] = pic.get("thumbnail_large") or pic.get("thumbnail")
                    d["image_credit"] = pic.get("photographer") or pic.get("credit")
                    d["image_link"] = pic.get("link")
                    d["image_is_art"] = False
                elif adb_img:
                    d["image_url"] = adb_img
                    d["image_credit"] = adb_credit
                    d["image_link"] = adb_link
                    d["image_is_art"] = False
                else:
                    d["image_url"] = f"/static/types/{slug}.png" if slug else None
                    d["image_credit"] = None
                    d["image_link"] = None
                    d["image_is_art"] = bool(slug)
            return {"ok": True, **d}
    return {"ok": False, "note": "No live detail available for this flight yet."}


@app.get("/api/photo/debug")
def photo_debug(reg: str):
    """Diagnostic: what does the Planespotters fetch return from THIS server?"""
    photos._cache.clear()
    result = photos.get_photo("", reg.upper())
    return {"reg": reg.upper(), "result": result, "diag": photos.LAST_DIAG}


@app.get("/api/diag/psprobe")
def photo_probe(reg: str = "N944WN"):
    """Bulletproof, self-contained probe: hit Planespotters directly from THIS
    server and report the raw HTTP status / body / exception, no layering."""
    import httpx
    import traceback
    reg = reg.upper()
    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
    out = {"reg": reg}
    for label, hdrs in (("browser_ua", {"User-Agent": ua, "Accept": "application/json"}),
                        ("no_ua", {})):
        url = f"https://api.planespotters.net/pub/photos/reg/{reg}"
        try:
            r = httpx.get(url, headers=hdrs, timeout=15, follow_redirects=True)
            out[label] = {"status": r.status_code, "len": len(r.text or ""),
                          "body": (r.text or "")[:300]}
        except Exception as e:  # noqa: BLE001
            out[label] = {"exception": f"{type(e).__name__}: {str(e)[:200]}",
                          "trace": traceback.format_exc()[-300:]}
    return out


@app.get("/api/flightaware/scan/status")
def flightaware_scan_status():
    """Progress of a background deep scan (for the UI's 'filling in…' state)."""
    return {**fa_mod.deep_status(), "budget_remaining": fa_mod.budget_remaining()}


@app.post("/api/schedule/load")
def schedule_load(background: BackgroundTasks, airport: str, hours: int = 72):
    """Load the full multi-day board for one airport. Prefers AeroDataBox (free,
    generous quota) so it never burns the FlightAware budget; falls back to a
    FlightAware deep scan only if no AeroDataBox key is configured."""
    icao = airport.upper()
    if adb_mod.available():
        r = adb_mod.load(icao, hours)      # fast + free (~1 call per 12h window)
        try:
            notify.dispatch_new()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "source": "aerodatabox", "async": False,
                "stored": r.get("stored", 0), "arrivals": r.get("arrivals", 0),
                "departures": r.get("departures", 0), "error": r.get("error")}
    # fallback: FlightAware deep scan in the background
    src = fa_mod.FlightAwareSource()
    if not src.available():
        return {"ok": False, "source": "none",
                "note": "No schedule source configured. Add an AeroDataBox key (free) "
                        "or a FlightAware key to load the day's board."}
    if not src.budget_ok():
        return {"ok": False, "source": "flightaware", "async": False,
                "note": "FlightAware monthly free budget reached — add a free AeroDataBox "
                        "key to keep loading the board (no charge), or wait for the reset."}
    if not fa_mod.deep_status().get("running"):
        background.add_task(_deep_scan_job, [icao])
    return {"ok": True, "source": "flightaware", "async": True,
            "note": "Loading via FlightAware in the background (paced to the free 5/min limit)."}


@app.get("/api/flightaware/debug/{icao}")
def flightaware_debug(icao: str, hours: int = 48, pages: int = 6):
    """Diagnostic: scan a wide look-ahead window of scheduled arrivals and report
    the time span covered, data completeness, and any notable/livery matches.
    Uses up to `pages` queries."""
    src = fa_mod.FlightAwareSource()
    if not src.available():
        return {"ok": False, "note": "no key"}
    if not src.budget_ok():
        return {"ok": False, "note": "budget reached"}
    now = int(time.time())
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
    end = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + hours * 3600))
    sched = src.scheduled_arrivals(icao.upper(), max_pages=pages, start=start, end=end)
    times = [f.get("scheduled_on") for f in sched if f.get("scheduled_on")]
    notable = []
    for fl in sched:
        ac = fa_mod._notable_for_flight(fl)
        if ac:
            notable.append({"ident": fl.get("ident"), "reg": fl.get("registration"),
                            "type": fl.get("type"), "origin": fl.get("origin"),
                            "cat": ac.get("category")})

    def _fmt(ts):
        return time.strftime("%Y-%m-%d %H:%MZ", time.gmtime(ts)) if ts else None
    return {"ok": True,
            "total_scheduled_seen": len(sched),
            "window_requested_hours": hours,
            "earliest": _fmt(min(times)) if times else None,
            "latest": _fmt(max(times)) if times else None,
            "hours_actually_covered": round((max(times) - now) / 3600, 1) if times else 0,
            "with_reg": sum(1 for f in sched if f.get("registration")),
            "notable_found": len(notable),
            "notable": notable[:40],
            "sample_types": list({f.get("type") for f in sched if f.get("type")})[:40],
            "budget_remaining": fa_mod.budget_remaining(),
            "last_error": fa_mod.last_error()}


@app.get("/api/flightaware/usage")
def flightaware_usage():
    """FlightAware free-tier budget status for the current month."""
    src = fa_mod.FlightAwareSource()
    return {"enabled": src.available(),
            "used": fa_mod.usage_this_month(),
            "budget": config.FA_MONTHLY_QUERY_BUDGET,
            "remaining": fa_mod.budget_remaining(),
            "last_error": fa_mod.last_error()}


class SearchQuery(BaseModel):
    type: str | None = None          # ICAO type code, e.g. A388
    registration: str | None = None  # tail or prefix
    operator: str | None = None      # ICAO operator, e.g. AAL


@app.post("/api/flightaware/search")
def flightaware_search(q: SearchQuery):
    """Global rare-jet search across the live worldwide fleet. Query-hungry, so
    it's manual-only and still respects the monthly budget cap."""
    src = fa_mod.FlightAwareSource()
    if not src.available():
        return {"ok": False, "note": "FlightAware key not set."}
    if not src.budget_ok():
        return {"ok": False, "note": "Monthly free-tier budget reached — try next month (no charge).", "results": []}
    parts = []
    if q.type:
        parts.append(f"-type {q.type.upper()}")
    if q.registration:
        parts.append(f"-idents {q.registration.upper()}")
    if q.operator:
        parts.append(f"-airline {q.operator.upper()}")
    if not parts:
        raise HTTPException(400, "Provide a type, registration, or operator to search.")
    results = fa_mod.FlightAwareSource().flight_search(" ".join(parts))
    return {"ok": True, "query": " ".join(parts), "count": len(results),
            "results": results, "budget_remaining": fa_mod.budget_remaining()}


class FollowIn(BaseModel):
    ident: str  # tail number or flight ident to follow anywhere


@app.post("/api/follow")
def follow_add(body: FollowIn):
    fa_mod.add_follow(body.ident)
    return {"ok": True, "follows": fa_mod.list_follows()}


@app.get("/api/follows")
def follow_list():
    return {"follows": fa_mod.list_follows()}


@app.post("/api/follow/remove")
def follow_remove(body: FollowIn):
    fa_mod.remove_follow(body.ident)
    return {"ok": True, "follows": fa_mod.list_follows()}


@app.post("/api/follow/check")
def follow_check():
    """Manual 'where are my followed aircraft now' — one query per followed tail."""
    src = fa_mod.FlightAwareSource()
    if not src.available():
        return {"ok": False, "note": "FlightAware key not set."}
    return {"ok": True, **fa_mod.check_follows(), "budget_remaining": fa_mod.budget_remaining()}


# ------------------------------------------- free live extras (no key/budget)
@app.get("/api/emergencies/{icao}")
def emergencies(icao: str, radius: int = Query(120, ge=10, le=250)):
    """Aircraft squawking 7500/7600/7700 near a tracked airport."""
    with db.get_conn() as conn:
        ap = _airport_row(conn, icao)
    if not ap:
        raise HTTPException(404, "unknown airport")
    return {"airport": icao.upper(),
            "emergencies": live_extras.emergencies_near(ap["lat"], ap["lon"], radius)}


@app.get("/api/overhead")
def overhead(lat: float = Query(...), lon: float = Query(...),
             radius: int = Query(20, ge=2, le=100)):
    """What's flying above a GPS point right now (notable ones flagged)."""
    return {"lat": lat, "lon": lon, "radius_nm": radius,
            "aircraft": live_extras.overhead_now(lat, lon, radius)}


@app.get("/api/weather/{icao}")
def weather(icao: str):
    """Current weather + likely active runway for an airport (free METAR)."""
    return live_extras.airport_weather(icao)


@app.get("/api/typeguide")
def typeguide():
    """The visual type guide — all aircraft illustrations grouped by category."""
    return {"types": type_art.manifest()}


# ---------------------------------------------------------------- web push
@app.get("/api/push/vapid-public-key")
def push_key():
    return {"publicKey": push.public_key(), **push.status()}


class PushSub(BaseModel):
    email: EmailStr
    subscription: dict


@app.post("/api/push/subscribe")
def push_subscribe(body: PushSub):
    ok = push.subscribe(body.email, body.subscription)
    if not ok:
        raise HTTPException(400, "invalid subscription")
    return {"ok": True, "devices": len(push.subscriptions_for(body.email))}


@app.post("/api/push/test/{email}")
def push_test(email: str):
    status, detail = push.send(email, "✈︎ SpotAlert test", "Push is working — you'll get alerts here.")
    return {"status": status, "detail": detail}


# ---------------------------------------------------------------- inbound ETA
@app.post("/api/eta/scan")
def eta_scan():
    """Scan live traffic for notable inbound aircraft and create ETA alerts."""
    result = eta_mod.scan_all()
    notify.dispatch_new()
    return result


class SimInbound(BaseModel):
    airport: str
    icao24: str
    dist_nm: float = 60
    gs: float = 420
    heading_offset: float = 0  # degrees off the direct bearing (0 = straight in)


@app.post("/api/eta/simulate")
def eta_simulate(sim: SimInbound):
    """Inject a synthetic inbound contact so you can SEE a predictive ETA alert
    without waiting for real traffic. Places the aircraft dist_nm out on a
    bearing straight toward the field, moving inbound at gs knots."""
    import math
    with db.get_conn() as conn:
        ap = conn.execute("SELECT * FROM airports WHERE icao=?", (sim.airport.upper(),)).fetchone()
        if not ap:
            raise HTTPException(404, "airport not covered")
        ap = dict(ap)
    # position the contact dist_nm away on a due-south bearing, tracking north toward field
    d_deg = sim.dist_nm / 60.0
    clat = ap["lat"] - d_deg
    clon = ap["lon"]
    track_to_field = eta_mod._bearing(clat, clon, ap["lat"], ap["lon"])
    contact = {"icao24": sim.icao24.lower(), "callsign": "SIM01",
               "lat": clat, "lon": clon, "gs": sim.gs, "alt": 18000,
               "track": (track_to_field + sim.heading_offset) % 360}
    with db.get_conn() as conn:
        r = eta_mod.scan_airport(conn, ap, contacts=[contact])
    dispatch = notify.dispatch_new()
    return {"airport": ap["icao"], "result": r, "dispatch": dispatch}


class SimDiversion(BaseModel):
    airport: str      # a field the operator rarely serves (e.g. KBFI, KPAE)
    icao24: str       # a scheduled airliner/cargo airframe (e.g. a1b2c3)


@app.post("/api/diversion/simulate")
def diversion_simulate(sim: SimDiversion):
    """Inject a scheduled carrier arriving at an off-network field, to see a
    diversion alert. Uses the real engine, so the detection is genuine."""
    ev = {"airport_icao": sim.airport.upper(), "icao24": sim.icao24.lower(),
          "direction": "arrival", "callsign": "DIV01", "event_time": int(time.time())}
    with db.get_conn() as conn:
        engine.record_visit(conn, ev)
        alert = engine.evaluate(conn, ev)
    dispatch = notify.dispatch_new()
    return {"alert": alert, "dispatch": dispatch}


# ---------------------------------------------------------------- watchlists
class WatchIn(BaseModel):
    email: EmailStr
    match_type: str            # 'tail' | 'type' | 'category'
    value: str
    region: str = "any"
    label: str = ""


@app.get("/api/watchlists/{email}")
def get_watchlists(email: str):
    return {"watchlists": watchlist.list_for(email)}


@app.post("/api/watchlists")
def add_watchlist(w: WatchIn):
    if w.match_type not in ("tail", "type", "category"):
        raise HTTPException(400, "match_type must be tail, type, or category")
    row = watchlist.add(w.email, w.match_type, w.value, w.region, w.label)
    return {"ok": True, "watchlist": row}


@app.delete("/api/watchlists/{email}/{wid}")
def del_watchlist(email: str, wid: int):
    watchlist.delete(email, wid)
    return {"ok": True}


# ---------------------------------------------------------------- logbook
class LogIn(BaseModel):
    email: EmailStr
    icao24: str | None = None
    registration: str | None = None
    typecode: str | None = None
    airport_icao: str | None = None
    seen_at: int | None = None
    notes: str = ""
    photo_url: str = ""


@app.get("/api/logbook/{email}")
def get_logbook(email: str):
    return {"entries": logbook.list_for(email), "stats": logbook.stats(email)}


@app.post("/api/logbook")
def add_logbook(entry: LogIn):
    lid = logbook.add(entry.email, entry.icao24, entry.registration, entry.typecode,
                      entry.airport_icao, entry.seen_at, entry.notes, entry.photo_url)
    return {"ok": True, "id": lid, "stats": logbook.stats(entry.email)}


@app.delete("/api/logbook/{email}/{log_id}")
def del_logbook(email: str, log_id: int):
    logbook.delete(email, log_id)
    return {"ok": True, "stats": logbook.stats(email)}


@app.post("/api/refresh")
def refresh(hours: int = Query(config.SCHEDULER_LOOKBACK_HOURS, ge=1, le=168)):
    """Run one full cycle now: pull live data (OpenSky + airplanes.live),
    re-evaluate rarity, and dispatch notifications. Same path the scheduler
    runs automatically."""
    total = scheduler.run_once(hours)
    if not sources.OpenSkySource().available():
        total["note"] = ("OpenSky credentials not set — running on airplanes.live "
                         "live layer + seed data only. Set OPENSKY_CLIENT_ID/SECRET "
                         "for full historical boards.")
    return total


# ------------------------------------------------------------------ UI
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"), media_type="application/javascript")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
