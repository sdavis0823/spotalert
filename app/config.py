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

# --- FlightAware AeroAPI (optional) — enables pre-takeoff / scheduled alerts ---
# Free Personal tier gives ~$5/month of queries. Get a key at
# https://www.flightaware.com/commercial/aeroapi/  (My AeroAPI -> API key).
FLIGHTAWARE_API_KEY = os.environ.get("FLIGHTAWARE_API_KEY", "")
FLIGHTAWARE_BASE = os.environ.get("FLIGHTAWARE_BASE", "https://aeroapi.flightaware.com/aeroapi")
# How far ahead to watch scheduled arrivals (hours). Airlines load schedules a
# day+ out; filed GA/military plans usually same-day.
SCHEDULED_LOOKAHEAD_HOURS = int(os.environ.get("SCHEDULED_LOOKAHEAD_HOURS", "36"))
# The scheduled scan is the paid call — run it less often than the live loop.
SCHEDULED_SCAN_INTERVAL_SEC = int(os.environ.get("SCHEDULED_SCAN_INTERVAL_SEC", "1800"))

# --- FlightAware query thrift (free Starter tier = 500 queries/month) ------
# One combined /airports/{id}/flights call per airport per scan covers
# scheduled arrivals+departures, enroute ETAs, diversions and cancellations.
# Default cadence = every 6h (~4 automatic scans/day per airport) so a handful
# of airports stay well inside the free Starter tier's 500 queries/month.
FA_AIRPORT_SCAN_INTERVAL_SEC = int(os.environ.get("FA_AIRPORT_SCAN_INTERVAL_SEC", "21600"))
# HARD monthly cap on FlightAware queries — the app refuses to call AeroAPI once
# this many have been made in the current calendar month, so you can NEVER be
# pushed past the free tier into a paid overage. Set below the real 500 limit.
FA_MONTHLY_QUERY_BUDGET = int(os.environ.get("FA_MONTHLY_QUERY_BUDGET", "450"))
# Global rare-jet fleet search is query-hungry — off unless explicitly enabled.
FA_SEARCH_ENABLED = os.environ.get("FA_SEARCH_ENABLED", "0") == "1"
# Follow-a-tail global tracking cadence.
FA_FOLLOW_SCAN_INTERVAL_SEC = int(os.environ.get("FA_FOLLOW_SCAN_INTERVAL_SEC", "1800"))

# --- Free live-feed extras (no key needed) --------------------------------
# Emergency squawk watch (7500 hijack / 7600 radio-fail / 7700 general).
EMERGENCY_SQUAWK_ENABLED = os.environ.get("EMERGENCY_SQUAWK_ENABLED", "1") == "1"
# aviationweather.gov METAR (free, no key) — powers weather panel + runway-in-use.
METAR_BASE = os.environ.get("METAR_BASE", "https://aviationweather.gov/api/data/metar")
