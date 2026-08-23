"""Web push (VAPID) — real browser/phone push notifications.

Design mirrors the SMTP channel: fully functional when the optional `pywebpush`
library is installed, and a safe logged dry-run when it isn't (so the app runs
anywhere). VAPID keys come from config; if none are set, an ephemeral P-256 pair
is generated at startup so push works out of the box in development.

Flow:
  * GET  /api/push/vapid-public-key  -> browser needs this to subscribe
  * POST /api/push/subscribe         -> store a PushSubscription for an email
  * notifier 'push' channel          -> send to that email's stored subscriptions

Sending encryption/JWT is handled by pywebpush; we only manage keys + storage.
"""
import base64
import json
import time

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from . import db, config

try:  # optional dependency — real sends need it
    from pywebpush import webpush, WebPushException  # type: ignore
    _HAVE_PYWEBPUSH = True
except Exception:  # noqa: BLE001
    _HAVE_PYWEBPUSH = False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


_KEYS = {"public": None, "private_pem": None}


def _ensure_keys():
    """Populate _KEYS with an application-server keypair.

    Uses configured keys if present; otherwise generates an ephemeral pair. The
    public key is the uncompressed EC point, base64url — exactly what the browser
    PushManager.subscribe() expects as applicationServerKey.
    """
    if _KEYS["public"]:
        return
    if config.VAPID_PUBLIC_KEY and config.VAPID_PRIVATE_KEY:
        _KEYS["public"] = config.VAPID_PUBLIC_KEY
        _KEYS["private_pem"] = config.VAPID_PRIVATE_KEY
        return
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    _KEYS["public"] = _b64(pub)
    _KEYS["private_pem"] = priv.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()


def public_key() -> str:
    _ensure_keys()
    return _KEYS["public"]


def status() -> dict:
    _ensure_keys()
    return {
        "configured": bool(config.VAPID_PUBLIC_KEY),
        "ephemeral": not bool(config.VAPID_PUBLIC_KEY),
        "can_send": _HAVE_PYWEBPUSH,
        "public_key": _KEYS["public"],
        "note": None if _HAVE_PYWEBPUSH else
                "pywebpush not installed — push runs in dry-run (logged). "
                "`pip install pywebpush` to send for real.",
    }


def subscribe(email: str, subscription: dict) -> bool:
    keys = subscription.get("keys") or {}
    endpoint = subscription.get("endpoint")
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        return False
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO push_subscriptions (email, endpoint, p256dh, auth, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 email=excluded.email, p256dh=excluded.p256dh, auth=excluded.auth""",
            (email, endpoint, keys["p256dh"], keys["auth"], int(time.time())))
    return True


def subscriptions_for(email: str) -> list[dict]:
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM push_subscriptions WHERE email=?", (email,)).fetchall()
    return [dict(r) for r in rows]


def send(email: str, title: str, body: str, url: str = "/") -> tuple[str, str]:
    """Send a push to every subscription for an email. Returns (status, detail).
    status: 'sent' | 'failed' | 'dry-run'."""
    _ensure_keys()
    subs = subscriptions_for(email)
    if not subs:
        return "dry-run", "no push subscriptions"
    if not _HAVE_PYWEBPUSH:
        return "dry-run", f"pywebpush absent — would push to {len(subs)} device(s)"

    payload = json.dumps({"title": title, "body": body, "url": url})
    ok = 0
    dead = []
    for s in subs:
        try:
            webpush(
                subscription_info={"endpoint": s["endpoint"],
                                   "keys": {"p256dh": s["p256dh"], "auth": s["auth"]}},
                data=payload,
                vapid_private_key=_KEYS["private_pem"],
                vapid_claims={"sub": config.VAPID_SUBJECT},
            )
            ok += 1
        except WebPushException as e:  # noqa: PERF203
            if getattr(e, "response", None) is not None and e.response.status_code in (404, 410):
                dead.append(s["endpoint"])  # gone — prune
        except Exception:  # noqa: BLE001
            pass
    if dead:
        with db.get_conn() as conn:
            for ep in dead:
                conn.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (ep,))
    return ("sent", f"pushed to {ok} device(s)") if ok else ("failed", "all sends failed")
