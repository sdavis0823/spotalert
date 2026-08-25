"""Online watches — "ping me when this flight/tail comes online".

Unlike the airport-centric watchlists (which fire when a plane shows up NEAR a
covered field), an online watch polls the live ADS-B feed by callsign or
registration for just the user's watched idents on each scheduler cycle, and
fires once each time a watch flips from offline -> online — anywhere in the
world. Because it only checks the handful of idents the user is watching (not
the whole sky), the per-tail lookups stay well within the free feed's limits.
"""
import time
import httpx
from . import db, live_extras

_UA = "SpotAlert/1.0 (+https://spotalert.onrender.com)"
_BASES = live_extras._ADSB_BASES   # reuse the open mirror list


def add(email, kind, value, label=""):
    kind = "tail" if kind == "tail" else "flight"
    v = (value or "").upper().replace(" ", "")
    if not v:
        return None
    now = int(time.time())
    with db.get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO online_watches
               (email, kind, value, label, last_online, active, created_at)
               VALUES (?,?,?,?,0,1,?)""",
            (email, kind, v, label or "", now))
        row = conn.execute(
            "SELECT * FROM online_watches WHERE email=? AND kind=? AND value=?",
            (email, kind, v)).fetchone()
    return dict(row) if row else None


def list_for(email):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM online_watches WHERE email=? ORDER BY id DESC",
            (email,)).fetchall()
    return [dict(r) for r in rows]


def delete(email, wid):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM online_watches WHERE id=? AND email=?", (wid, email))


def delete_by_value(email, kind, value):
    v = (value or "").upper().replace(" ", "")
    with db.get_conn() as conn:
        conn.execute("DELETE FROM online_watches WHERE email=? AND kind=? AND value=?",
                     (email, kind, v))


def _probe(kind, value):
    """The live aircraft dict if this flight/tail is broadcasting right now, else
    None. Tries each open mirror in turn (fast enough as a background check)."""
    if kind == "flight":
        cs = live_extras._ident_to_callsign(value)
        if not cs:
            return None
        path = f"/callsign/{cs}"
    else:
        path = f"/registration/{value}"
    for base in _BASES:
        try:
            with httpx.Client(timeout=8, headers={"User-Agent": _UA}) as c:
                r = c.get(f"{base}{path}")
                if r.status_code != 200:
                    continue
                data = r.json()
        except (httpx.HTTPError, ValueError):
            continue
        for a in (data.get("ac") or []):
            return a
    return None


def _blurb(ac):
    reg = (ac.get("r") or "").strip()
    alt = ac.get("alt_baro")
    if alt == "ground":
        where = "on the ground"
    elif isinstance(alt, (int, float)):
        where = f"{int(alt):,} ft"
    else:
        where = "airborne"
    return " · ".join(x for x in (reg, where) if x)


def check_all():
    """Poll every active watch. Returns the watches that JUST came online (each
    with a human 'blurb' + reg), and updates every watch's stored online state."""
    now = int(time.time())
    fired = []
    with db.get_conn() as conn:
        watches = [dict(r) for r in conn.execute(
            "SELECT * FROM online_watches WHERE active=1").fetchall()]
    for w in watches:
        ac = _probe(w["kind"], w["value"])
        online = 1 if ac else 0
        if online and not w["last_online"]:
            fired.append({**w, "blurb": _blurb(ac),
                          "reg": (ac.get("r") or "").strip()})
        if online != w["last_online"]:
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE online_watches SET last_online=?, last_seen=? WHERE id=?",
                    (online, now, w["id"]))
    return fired
