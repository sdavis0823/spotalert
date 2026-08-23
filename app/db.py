"""SQLite data layer for the JetTip clone.

Schema
------
airports        : covered airports (ICAO/IATA, name, location)
aircraft        : icao24 -> registration, type, operator, category + interest tags
visits          : one row per observed arrival/departure event at an airport
alerts          : generated alerts (derived from visits + rarity engine)
subscriptions   : a user + their monitored airports and alert preferences
"""
import sqlite3
from contextlib import contextmanager
from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS airports (
    icao        TEXT PRIMARY KEY,       -- e.g. KSEA
    iata        TEXT,                   -- e.g. SEA
    name        TEXT NOT NULL,
    city        TEXT,
    lat         REAL,
    lon         REAL
);

CREATE TABLE IF NOT EXISTS aircraft (
    icao24      TEXT PRIMARY KEY,       -- 24-bit ICAO hex, lowercase
    registration TEXT,                  -- tail number, e.g. N705GT
    typecode    TEXT,                   -- ICAO type, e.g. B77W
    model       TEXT,                   -- human model, e.g. Boeing 777-300ER
    operator    TEXT,                   -- airline / owner
    category    TEXT,                   -- airliner|private|cargo|military|ga|special|warbird|tanker|testbed|gov
    interest_tags TEXT DEFAULT '',      -- comma list e.g. 'special-livery,retro'
    base_interest INTEGER DEFAULT 0     -- 1 if inherently interesting regardless of rarity
);

CREATE TABLE IF NOT EXISTS visits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    airport_icao TEXT NOT NULL,
    icao24      TEXT NOT NULL,
    direction   TEXT NOT NULL,          -- 'arrival' | 'departure'
    callsign    TEXT,
    event_time  INTEGER NOT NULL,       -- unix seconds
    UNIQUE(airport_icao, icao24, direction, event_time)
);
CREATE INDEX IF NOT EXISTS idx_visits_lookup
    ON visits(airport_icao, icao24, event_time);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    airport_icao TEXT NOT NULL,
    icao24      TEXT NOT NULL,
    direction   TEXT NOT NULL,
    callsign    TEXT,
    event_time  INTEGER NOT NULL,
    priority    TEXT NOT NULL,          -- 'red' | 'blue'
    reason      TEXT NOT NULL,          -- human explanation
    visit_count INTEGER NOT NULL,       -- visits in trailing window at time of alert
    created_at  INTEGER NOT NULL,
    eta_minutes INTEGER,                -- set for direction='inbound' predictive alerts
    diversion   INTEGER DEFAULT 0,      -- 1 if this arrival looks like a diversion
    UNIQUE(airport_icao, icao24, direction, event_time)
);
CREATE INDEX IF NOT EXISTS idx_alerts_airport ON alerts(airport_icao, event_time);

CREATE TABLE IF NOT EXISTS subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT UNIQUE NOT NULL,
    airports    TEXT DEFAULT '',        -- comma list of ICAO codes (max enforced in app)
    want_red    INTEGER DEFAULT 1,
    want_blue   INTEGER DEFAULT 1,
    want_diversions INTEGER DEFAULT 0,
    webhook_url TEXT DEFAULT '',        -- Slack/Discord/custom incoming webhook (optional)
    want_email  INTEGER DEFAULT 1,      -- deliver via email channel
    categories  TEXT DEFAULT '',        -- optional comma list to restrict (e.g. 'military,warbird'); empty = all
    active      INTEGER DEFAULT 1
);

-- Every dispatched (or attempted) notification, for dedup + an in-app feed.
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,          -- recipient (dedup key)
    alert_id    INTEGER NOT NULL,
    channel     TEXT NOT NULL,          -- 'email' | 'webhook' | 'console'
    status      TEXT NOT NULL,          -- 'sent' | 'failed' | 'dry-run'
    source      TEXT DEFAULT 'subscription',  -- 'subscription' | 'watchlist'
    detail      TEXT,
    sent_at     INTEGER NOT NULL,
    UNIQUE(email, alert_id, channel)
);
CREATE INDEX IF NOT EXISTS idx_notif_email ON notifications(email, sent_at);

-- Watchlists: follow a specific tail, a type, or a category, across a region.
CREATE TABLE IF NOT EXISTS watchlists (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    match_type  TEXT NOT NULL,          -- 'tail' | 'type' | 'category'
    value       TEXT NOT NULL,          -- e.g. 'N1KE' | 'A388' | 'warbird'
    region      TEXT DEFAULT 'any',     -- 'any' or comma list of airport ICAOs
    label       TEXT DEFAULT '',
    active      INTEGER DEFAULT 1,
    created_at  INTEGER NOT NULL,
    UNIQUE(email, match_type, value, region)
);
CREATE INDEX IF NOT EXISTS idx_watch_email ON watchlists(email);

-- Spotting logbook: frames the user has personally caught / photographed.
CREATE TABLE IF NOT EXISTS logbook (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    icao24      TEXT,
    registration TEXT,
    typecode    TEXT,
    airport_icao TEXT,
    seen_at     INTEGER NOT NULL,
    notes       TEXT DEFAULT '',
    photo_url   TEXT DEFAULT '',
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_email ON logbook(email, seen_at);

-- Web push subscriptions (one browser/device per row).
CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    email       TEXT NOT NULL,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_push_email ON push_subscriptions(email);
"""


def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_conn():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {config.DB_PATH}")
