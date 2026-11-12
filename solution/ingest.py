"""Drop-level orchestration: discover files, skip what was already ingested, one transaction per file."""
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .events import ingest_event_file
from .leads import ingest_lead_file
from .schemas import Config
from .security import Vault


@dataclass
class FileResult:
    rel_path: str
    status: str  # ingested | skipped_already_ingested | skipped_changed | skipped_no_schema
    detail: str = ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _discover(drop: Path):
    """Yield (kind, workflow_id | None, path) in the order the spec asks for:
    leads (workflow dirs, then filename order) and then events (filename order)."""
    leads_dir = drop / "leads"
    if leads_dir.is_dir():
        for wf_dir in sorted(p for p in leads_dir.iterdir() if p.is_dir()):
            for f in sorted(wf_dir.glob("*.csv")):
                yield "leads", wf_dir.name, f
    events_dir = drop / "events"
    if events_dir.is_dir():
        for f in sorted(events_dir.glob("*.jsonl")):
            yield "events", None, f


def ingest_drop(conn: sqlite3.Connection, vault: Vault, cfg: Config, drop_dir: str | Path) -> list[FileResult]:
    drop = Path(drop_dir)
    if not drop.is_dir():
        raise FileNotFoundError(f"drop directory not found: {drop}")
    drop_name = drop.resolve().name
    results: list[FileResult] = []

    for kind, wf, path in _discover(drop):
        rel = path.relative_to(drop).as_posix()
        digest = _sha256(path)
        prev = conn.execute("SELECT sha256 FROM ingested_files WHERE drop_name=? AND rel_path=?",
                            (drop_name, rel)).fetchone()
        if prev is not None:
            if prev[0] == digest:
                results.append(FileResult(rel, "skipped_already_ingested"))
            else:
                results.append(FileResult(rel, "skipped_changed",
                                          "content differs from what was ingested; ignored to keep the ledger consistent"))
            continue
        if kind == "leads" and wf not in cfg.schemas:
            results.append(FileResult(rel, "skipped_no_schema", f"no schema for workflow {wf!r}"))
            continue

        batch_id = path.stem if kind == "leads" else None
        # `with conn` = one atomic transaction per file: a crash leaves no half-ingested file.
        with conn:
            cur = conn.execute(
                "INSERT INTO ingested_files (kind, drop_name, rel_path, workflow_id, batch_id, sha256, ingested_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (kind, drop_name, rel, wf, batch_id, digest, datetime.now(timezone.utc).isoformat()))
            file_id = cur.lastrowid
            if kind == "leads":
                s = ingest_lead_file(conn, vault, cfg.schemas[wf], path, file_id, batch_id)
                conn.execute(
                    "UPDATE ingested_files SET rows_total=?, rows_rejected=?, duplicates_merged=?, field_warnings=?"
                    " WHERE id=?", (s.rows_total, s.rows_rejected, s.duplicates_merged, s.field_warnings, file_id))
                detail = (f"{s.rows_total} rows, {s.leads_created} new leads, {s.duplicates_merged} merged, "
                          f"{s.rows_rejected} rejected")
            else:
                e = ingest_event_file(conn, vault, path, file_id)
                conn.execute(
                    "UPDATE ingested_files SET rows_total=?, malformed_events=?, duplicate_events=? WHERE id=?",
                    (e.lines_total, e.malformed, e.duplicates, file_id))
                detail = f"{e.lines_total} lines, {e.stored} stored, {e.duplicates} duplicate, {e.malformed} malformed"
        results.append(FileResult(rel, "ingested", detail))
    return results
