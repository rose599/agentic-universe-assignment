"""Event-file ingestion into the append-only ledger."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .normalize import normalize_phone, parse_timestamp
from .security import Vault

TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"  # fixed width => lexicographic order == chronological order


@dataclass
class EventStats:
    lines_total: int = 0
    malformed: int = 0
    duplicates: int = 0
    stored: int = 0


def _s(v):
    return v if isinstance(v, str) and v != "" else None


def ingest_event_file(conn: sqlite3.Connection, vault: Vault, path: Path, file_id: int) -> EventStats:
    stats = EventStats()
    insert = (
        "INSERT OR IGNORE INTO events (event_id, event_type, workflow_id, call_id, occurred_at,"
        " phone_hash, disposition_code, payload, file_id) VALUES (?,?,?,?,?,?,?,?,?)")
    with path.open(encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            stats.lines_total += 1
            try:
                ev = json.loads(line)
            except ValueError:
                stats.malformed += 1
                continue
            if not isinstance(ev, dict) or ev.get("event_id") in (None, ""):
                stats.malformed += 1
                continue
            ts = parse_timestamp(ev.get("occurred_at"))
            if ts is None:
                # Not covered by the spec; an event we cannot place in time is unusable.
                stats.malformed += 1
                continue

            payload = ev.get("payload") if isinstance(ev.get("payload"), dict) else {}
            etype = _s(ev.get("event_type"))
            phone = normalize_phone(ev.get("phone")) if ev.get("phone") is not None else None
            code = payload.get("disposition_code") if etype and etype.startswith("disposition.") else None
            cur = conn.execute(insert, (
                str(ev["event_id"]), etype, _s(ev.get("workflow_id")), _s(ev.get("call_id")),
                ts.strftime(TS_FORMAT), vault.phone_hash(phone) if phone else None,
                code if isinstance(code, str) else None,
                json.dumps(payload, sort_keys=True), file_id))
            if cur.rowcount == 0:
                stats.duplicates += 1  # event_id already seen (this file or any earlier one)
            else:
                stats.stored += 1
    return stats
