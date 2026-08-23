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
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr

from . import db, config, aircraft as ac_mod, engine
from . import sources, notify, scheduler, eta as eta_mod, watchlist, logbook, photos, push, liveries

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
    }


# ------------------------------------------------------------------ routes
@app.get("/api/airports")
def list_airports():
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM airports ORDER BY icao").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/board/{icao}")
def board(icao: str, hours: int = Query(48, ge=1, le=720),
          direction: str | None = None, category: str | None = None,
          notable_only: bool = False):
    icao = icao.upper()
    since = int(time.time()) - hours * 3600
    with db.get_conn() as conn:
        if not _airport_row(conn, icao):
            raise HTTPException(404, f"Airport {icao} not covered")
        q = "SELECT * FROM visits WHERE airport_icao=? AND event_time>=?"
        params = [icao, since]
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
            out.append(e)
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
