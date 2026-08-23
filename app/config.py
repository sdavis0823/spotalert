"""Configuration for the JetTip clone ('SpotAlert').

All settings can be overridden via environment variables. The app runs fully
with the bundled seed data and NO credentials; supplying OpenSky OAuth2
client credentials switches ingestion to live data.
"""
import os

# --- OpenSky OAuth2 client credentials (optional) -------------------------
# Register a free account at https://opensky-network.org/ and create an API
# client to obtain these. Without them the app serves the bundled seed data.
OPENSKY_CLIENT_ID = os.environ.get("OPENSKY_CLIENT_ID", "")
OPENSKY_CLIENT_SECRET = os.environ.get("OPENSKY_CLIENT_SECRET", "")
OPENSKY_TOKEN_URL = os.environ.get(
    "OPENSKY_TOKEN_URL",
    "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token",
)
OPENSKY_API_BASE = os.environ.get("OPENSKY_API_BASE", "https://opensky-network.org/api")

# --- Rarity engine tuning -------------------------------------------------
# An airframe is "unusual" at an airport based on how many times it has
# visited within the trailing window.
RARITY_WINDOW_DAYS = int(os.environ.get("RARITY_WINDOW_DAYS", "30"))
RED_MAX_VISITS = int(os.environ.get("RED_MAX_VISITS", "2"))   # <= this -> high priority (red)
BLUE_MAX_VISITS = int(os.environ.get("BLUE_MAX_VISITS", "4")) # <= this (and > RED) -> low priority (blue)

# Max airports a single subscription may monitor (matches JetTip).
MAX_AIRPORTS_PER_SUB = int(os.environ.get("MAX_AIRPORTS_PER_SUB", "10"))

# Path to the SQLite database file.
DB_PATH = os.environ.get("JETTIP_DB", os.path.join(os.path.dirname(__file__), "jettip.db"))

# --- Notification delivery ------------------------------------------------
# SMTP is optional. With no SMTP host set, email delivery runs in DRY-RUN mode:
# the notification is matched, rendered, and logged, but nothing is actually
# sent — so the full pipeline is testable with zero external config and no risk
# of emailing real people by accident.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "SpotAlert <alerts@spotalert.local>")
SMTP_STARTTLS = os.environ.get("SMTP_STARTTLS", "1") == "1"

# Global kill-switch: force dry-run even if SMTP/webhooks are configured.
NOTIFY_DRY_RUN = os.environ.get("NOTIFY_DRY_RUN", "0") == "1"

# --- Background scheduler -------------------------------------------------
SCHEDULER_ENABLED = os.environ.get("SCHEDULER_ENABLED", "1") == "1"
SCHEDULER_INTERVAL_SEC = int(os.environ.get("SCHEDULER_INTERVAL_SEC", "300"))  # 5 min
SCHEDULER_LOOKBACK_HOURS = int(os.environ.get("SCHEDULER_LOOKBACK_HOURS", "6"))

# --- Web push (VAPID) -----------------------------------------------------
# Provide a stable keypair in production (generate once, keep the private key
# secret — reference a secret store, never commit it). If unset, an ephemeral
# pair is generated at startup so push works in dev; subscriptions from a prior
# keypair stop validating when the key rotates, which is fine for dev.
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:alerts@spotalert.local")
