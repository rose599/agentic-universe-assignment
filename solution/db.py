"""SQLite schema and connection handling.

Tables
------
ingested_files  one row per source file ever ingested (+ the per-file counters that feed
                data_quality.json). UNIQUE(drop_name, rel_path) is what makes re-ingest a no-op.
leads           one row per (workflow_id, phone). PII is encrypted; phone is matched through
                `phone_hash` (keyed HMAC blind index). Non-PII fields stay queryable inside JSON.
rejections      one row per rejected lead row. Reason only: no PII.
events          the append-only interaction ledger. Raw events are never edited or deleted
                (triggers enforce it). Overrides are just more rows. Nothing about *linking*
                is stored: leads are resolved at report time, which is why an event that
                arrived before its lead is picked up automatically.
"""
from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS ingested_files (
    id                INTEGER PRIMARY KEY,
    kind              TEXT NOT NULL CHECK (kind IN ('leads', 'events')),
    drop_name         TEXT NOT NULL,
    rel_path          TEXT NOT NULL,
    workflow_id       TEXT,
    batch_id          TEXT,
    sha256            TEXT NOT NULL,
    ingested_at       TEXT NOT NULL,
    rows_total        INTEGER NOT NULL DEFAULT 0,   -- lead rows (non-blank) / event lines (non-empty)
    rows_rejected     INTEGER NOT NULL DEFAULT 0,
    duplicates_merged INTEGER NOT NULL DEFAULT 0,
    field_warnings    INTEGER NOT NULL DEFAULT 0,
    malformed_events  INTEGER NOT NULL DEFAULT 0,
    duplicate_events  INTEGER NOT NULL DEFAULT 0,
    UNIQUE (drop_name, rel_path)
);

CREATE TABLE IF NOT EXISTS leads (
    id             INTEGER PRIMARY KEY,
    workflow_id    TEXT NOT NULL,
    phone_hash     TEXT NOT NULL,          -- HMAC-SHA256 blind index of '+91XXXXXXXXXX'
    phone_last4    TEXT NOT NULL,          -- only used for the masked phone in reports
    batch_id       TEXT NOT NULL,          -- file stem of the FIRST valid row; never changes
    source_file_id INTEGER NOT NULL REFERENCES ingested_files(id),
    fields         TEXT NOT NULL,          -- JSON; pii fields hold Fernet tokens
    UNIQUE (workflow_id, phone_hash)
);

CREATE TABLE IF NOT EXISTS rejections (
    id          INTEGER PRIMARY KEY,
    file_id     INTEGER NOT NULL REFERENCES ingested_files(id),
    workflow_id TEXT NOT NULL,
    batch_id    TEXT NOT NULL,
    row_number  INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    UNIQUE (file_id, row_number)
);

CREATE TABLE IF NOT EXISTS events (
    seq              INTEGER PRIMARY KEY,   -- arrival order
    event_id         TEXT NOT NULL UNIQUE,  -- dedupe key, across all drops
    event_type       TEXT,
    workflow_id      TEXT,
    call_id          TEXT,
    occurred_at      TEXT NOT NULL,         -- normalised to UTC, fixed-width ISO-8601 (sortable)
    phone_hash       TEXT,                  -- NULL for overrides / unusable phones
    disposition_code TEXT,
    payload          TEXT NOT NULL,         -- JSON as received
    file_id          INTEGER NOT NULL REFERENCES ingested_files(id)
);
CREATE INDEX IF NOT EXISTS idx_events_call ON events(call_id);

CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events is append-only'); END;
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.executescript(SCHEMA)
    return conn
