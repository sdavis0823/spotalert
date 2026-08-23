"""Notification delivery — match new alerts to subscriptions and dispatch.

Channels:
  * email   — SMTP if configured, otherwise DRY-RUN (rendered + logged only).
  * webhook — POST a JSON payload to a Slack/Discord/custom incoming webhook.
  * console — always-on fallback; prints + logs (useful for local dev/testing).

Matching rules per subscription:
  * airport must be in the subscription's airport list,
  * priority must be wanted (want_red / want_blue),
  * category filter (if set) must include the aircraft's category.

Every dispatch is recorded in `notifications` with UNIQUE(sub, alert, channel)
so an alert is never delivered twice on the same channel (idempotent).
"""
import time
import json
import smtplib
import ssl
from email.message import EmailMessage
import httpx

from . import db, config, aircraft as ac_mod, watchlist, push


# --------------------------------------------------------------------------
def _wants(sub, alert) -> bool:
    airports = [a for a in (sub["airports"] or "").split(",") if a]
    if alert["airport_icao"] not in airports:
        return False
    is_div = bool(alert["diversion"]) if "diversion" in alert.keys() else False
    # A diversion opt-in delivers diversions regardless of priority prefs.
    if not (is_div and sub["want_diversions"]):
        if alert["priority"] == "red" and not sub["want_red"]:
            return False
        if alert["priority"] == "blue" and not sub["want_blue"]:
            return False
    cats = [c for c in (sub["categories"] or "").split(",") if c]
    if cats:
        ac = ac_mod.classify(ac_mod.get_aircraft(alert["icao24"]))
        if ac.get("category") not in cats:
            return False
    return True


def _render(alert) -> tuple[str, str]:
    ac = ac_mod.classify(ac_mod.get_aircraft(alert["icao24"]))
    name = ac_mod.display_name(ac)
    when = time.strftime("%b %d %H:%M", time.localtime(alert["event_time"]))
    flag = " [BLOCKED/NOTABLE]" if ac.get("is_blocked") else ""
    subject = f"✈︎ {alert['priority'].upper()} @ {alert['airport_icao']}: {name}{flag}"
    body = (
        f"{name}\n"
        f"{alert['direction'].title()} at {alert['airport_icao']} — {when}\n"
        f"Priority: {alert['priority'].upper()}\n"
        f"Why: {alert['reason']}\n"
        f"Category: {ac.get('category')}  Tags: {', '.join(ac.get('interest_tags') or []) or '—'}\n"
        f"Visits in window: {alert['visit_count']}\n"
    )
    return subject, body


# ------------------------------------------------------------- channels
def _send_email(to_addr, subject, body) -> tuple[str, str]:
    if config.NOTIFY_DRY_RUN or not config.SMTP_HOST:
        return "dry-run", "SMTP not configured (or dry-run) — rendered only"
    msg = EmailMessage()
    msg["From"] = config.SMTP_FROM
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
            if config.SMTP_STARTTLS:
                s.starttls(context=ssl.create_default_context())
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
        return "sent", f"emailed {to_addr}"
    except Exception as e:  # noqa: BLE001
        return "failed", f"{type(e).__name__}: {e}"


def _send_webhook(url, subject, body, alert) -> tuple[str, str]:
    if config.NOTIFY_DRY_RUN:
        return "dry-run", "dry-run — webhook not posted"
    payload = {"text": f"*{subject}*\n{body}", "alert": {
        "airport": alert["airport_icao"], "icao24": alert["icao24"],
        "priority": alert["priority"], "reason": alert["reason"],
        "event_time": alert["event_time"]}}
    try:
        r = httpx.post(url, json=payload, timeout=15)
        r.raise_for_status()
        return "sent", f"POST {r.status_code}"
    except Exception as e:  # noqa: BLE001
        return "failed", f"{type(e).__name__}: {e}"


# ------------------------------------------------------------- dispatch
def _deliver(conn, email, alert, channels, source, webhook_url, counters):
    """Deliver one alert to one recipient across channels (idempotent per email)."""
    now = int(time.time())
    subject, body = _render(alert)
    for ch in channels:
        exists = conn.execute(
            "SELECT 1 FROM notifications WHERE email=? AND alert_id=? AND channel=?",
            (email, alert["id"], ch)).fetchone()
        if exists:
            counters["already_delivered"] += 1
            continue
        if ch == "email":
            status, detail = _send_email(email, subject, body)
        elif ch == "webhook":
            status, detail = _send_webhook(webhook_url, subject, body, alert)
        elif ch == "push":
            status, detail = push.send(email, subject, body)
        else:
            status, detail = "sent", "console"
            print(f"[SpotAlert->{email}] {subject}")
        conn.execute(
            """INSERT OR IGNORE INTO notifications
               (email, alert_id, channel, status, source, detail, sent_at)
               VALUES (?,?,?,?,?,?,?)""",
            (email, alert["id"], ch, status, source, detail, now))
        counters[{"sent": "sent", "failed": "failed"}.get(status, "dry_run")] += 1


def dispatch_new(limit_alerts: int = 500) -> dict:
    """Deliver undelivered alerts to matching subscriptions AND watchlists.

    Idempotent via the notifications table (unique per email+alert+channel).
    """
    counters = {"sent": 0, "failed": 0, "dry_run": 0, "already_delivered": 0, "watchlist_hits": 0}
    with db.get_conn() as conn:
        subs = [dict(r) for r in conn.execute(
            "SELECT * FROM subscriptions WHERE active=1").fetchall()]
        recent = [dict(r) for r in conn.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit_alerts,)).fetchall()]

        # 1) subscription matches
        for sub in subs:
            channels = []
            if sub["want_email"]:
                channels.append("email")
            if sub["webhook_url"]:
                channels.append("webhook")
            if push.subscriptions_for(sub["email"]):
                channels.append("push")
            if not channels:
                channels.append("console")
            for alert in recent:
                if _wants(sub, alert):
                    _deliver(conn, sub["email"], alert, channels, "subscription",
                             sub["webhook_url"], counters)

        # 2) watchlist matches (tail / type / category across a region)
        sub_by_email = {s["email"]: s for s in subs}
        for alert in recent:
            for w in watchlist.matches(conn, alert):
                counters["watchlist_hits"] += 1
                s = sub_by_email.get(w["email"])
                channels = ["email"]
                webhook = ""
                if s and s["webhook_url"]:
                    channels.append("webhook")
                    webhook = s["webhook_url"]
                _deliver(conn, w["email"], alert, channels, "watchlist", webhook, counters)

    return counters
