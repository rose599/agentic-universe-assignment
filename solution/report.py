"""Report generation.

Everything is *derived* from the ledger + leads tables at report time, so the output only
depends on the set of ingested data, never on the order or number of `ingest` runs. That is
what makes "drop_1 then drop_2" equal to "everything at once", and what lets an event that
arrived before its lead count the moment the lead shows up.
"""
from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .events import TS_FORMAT
from .normalize import IST
from .schemas import Config

BUCKETS = ("yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed")


@dataclass
class Call:
    lead_id: int
    call_id: str
    initiated: datetime | None = None
    connected: bool = False
    telephony_failed: bool = False
    # (occurred_at, event_id, code) -- latest wins; event_id only breaks exact timestamp ties
    sets: list = field(default_factory=list)
    overrides: list = field(default_factory=list)

    def effective_disposition(self) -> str | None:
        pool = self.overrides or self.sets
        return max(pool)[2] if pool else None


def _parse_utc(s: str) -> datetime:
    return datetime.strptime(s, TS_FORMAT).replace(tzinfo=timezone.utc)


def build_report(conn: sqlite3.Connection, cfg: Config) -> dict:
    # ---- leads -----------------------------------------------------------------------------
    leads: dict[int, dict] = {}
    by_identity: dict[tuple[str, str], int] = {}
    for lid, wf, phash, batch, last4, fields_json in conn.execute(
            "SELECT id, workflow_id, phone_hash, batch_id, phone_last4, fields FROM leads ORDER BY id"):
        schema = cfg.schemas.get(wf)
        ref_f = schema.reference_field if schema else None
        fields = json.loads(fields_json)
        leads[lid] = {"wf": wf, "batch": batch, "last4": last4,
                      "ref": fields.get(ref_f.name, "") if ref_f else ""}
        by_identity[(wf, phash)] = lid

    # ---- link events to leads --------------------------------------------------------------
    calls: dict[tuple[int, str], Call] = {}
    call_to_lead: dict[str, int] = {}
    orphan = unknown = 0
    pending_overrides = []

    def get_call(lid: int, cid: str) -> Call:
        c = calls.get((lid, cid))
        if c is None:
            c = calls[(lid, cid)] = Call(lid, cid)
        return c

    def add_disposition(call: Call, etype: str, ts: datetime, eid: str, code: str | None, wf: str):
        nonlocal unknown
        if code is None or (wf, code) not in cfg.dispositions:
            unknown += 1  # unknown disposition: counted and ignored
            return
        (call.overrides if etype == "disposition.override" else call.sets).append((ts, eid, code))

    rows = conn.execute(
        "SELECT event_id, event_type, workflow_id, call_id, occurred_at, phone_hash, disposition_code"
        " FROM events ORDER BY seq")
    for eid, etype, wf, cid, ts_s, phash, code in rows:
        if etype == "disposition.override":
            pending_overrides.append((eid, etype, wf, cid, ts_s, phash, code))
            continue
        lid = by_identity.get((wf, phash)) if (wf and phash) else None
        if lid is None:
            orphan += 1
            continue
        if cid is None:
            continue  # linked, but with no call_id it cannot belong to any call
        call_to_lead.setdefault(cid, lid)
        call = get_call(lid, cid)
        ts = _parse_utc(ts_s)
        if etype == "call.initiated":
            if call.initiated is None or ts < call.initiated:
                call.initiated = ts
        elif etype == "call.connected":
            call.connected = True
        elif etype == "call.telephony_failed":
            call.telephony_failed = True
        elif etype == "disposition.set":
            add_disposition(call, etype, ts, eid, code, leads[lid]["wf"])

    # Overrides carry no phone: they link through the lead of the call's other events.
    for eid, etype, wf, cid, ts_s, phash, code in pending_overrides:
        lid = call_to_lead.get(cid) if cid else None
        if lid is None and wf and phash:
            lid = by_identity.get((wf, phash))  # defensive: an override that does carry a phone
        if lid is None:
            orphan += 1
            continue
        if cid is None:
            continue
        add_disposition(get_call(lid, cid), etype, _parse_utc(ts_s), eid, code, leads[lid]["wf"])

    # ---- derive per-lead / per-day facts ----------------------------------------------------
    calls_by_lead: dict[int, list[Call]] = defaultdict(list)
    for c in calls.values():
        if c.initiated is not None:  # a call exists only if its call.initiated is present
            calls_by_lead[c.lead_id].append(c)

    def disp(wf: str, code: str | None):
        return cfg.dispositions.get((wf, code)) if code else None

    lead_rows, batch_counts = [], defaultdict(lambda: dict.fromkeys(("total_leads", *BUCKETS), 0))
    daily = defaultdict(lambda: {"total": 0, "connected": 0, "success": set()})

    for lid, L in leads.items():
        wf = L["wf"]
        cs = sorted(calls_by_lead.get(lid, []), key=lambda c: (c.initiated, c.call_id))
        eff = [(c, c.effective_disposition()) for c in cs]
        ever_success = any((d := disp(wf, code)) and d.is_success for _, code in eff)
        if not cs:
            bucket = "yet_to_reach_out"
        elif any((d := disp(wf, code)) and d.is_terminal for _, code in eff):
            bucket = "completed"
        elif all(c.telephony_failed for c in cs):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"
        current = next((code for c, code in reversed(eff) if code), "")
        lead_rows.append({
            "workflow_id": wf, "external_ref": L["ref"], "batch_id": L["batch"],
            "phone_masked": "+91******" + L["last4"], "bucket": bucket,
            "total_calls": len(cs), "connected_calls": sum(c.connected for c in cs),
            "last_call_at": cs[-1].initiated.astimezone(IST).isoformat(timespec="seconds") if cs else "",
            "current_disposition": current, "ever_success": "true" if ever_success else "false",
            "_id": lid})
        b = batch_counts[(wf, L["batch"])]
        b["total_leads"] += 1
        b[bucket] += 1
        for c, code in eff:
            day = daily[(wf, c.initiated.astimezone(IST).date().isoformat())]
            day["total"] += 1
            day["connected"] += c.connected
            if (d := disp(wf, code)) and d.is_success:
                day["success"].add(lid)

    lead_rows.sort(key=lambda r: (r["workflow_id"], r["external_ref"], r["_id"]))

    # ---- data quality -----------------------------------------------------------------------
    def total(col: str, kind: str) -> int:
        return conn.execute(f"SELECT COALESCE(SUM({col}),0) FROM ingested_files WHERE kind=?", (kind,)).fetchone()[0]

    by_reason = dict(conn.execute("SELECT reason, COUNT(*) FROM rejections GROUP BY reason ORDER BY reason"))
    quality = {
        "lead_rows_total": total("rows_total", "leads"),
        "lead_rows_rejected": total("rows_rejected", "leads"),
        "rejections_by_reason": by_reason,
        "duplicate_lead_rows_merged": total("duplicates_merged", "leads"),
        "field_warnings": total("field_warnings", "leads"),
        "events_total": total("rows_total", "events"),
        "malformed_events": total("malformed_events", "events"),
        "duplicate_events_dropped": total("duplicate_events", "events"),
        "orphan_events": orphan,
        "unknown_disposition_events": unknown,
    }
    return {"leads": lead_rows, "batches": batch_counts, "daily": daily, "quality": quality}


def _write_csv(path: Path, header: list[str], rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_reports(conn: sqlite3.Connection, cfg: Config, out_dir: str | Path) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    r = build_report(conn, cfg)

    _write_csv(out / "batch_summary.csv",
               ["workflow_id", "batch_id", "total_leads", *BUCKETS],
               ([wf, batch, c["total_leads"], *(c[b] for b in BUCKETS)]
                for (wf, batch), c in sorted(r["batches"].items())))

    _write_csv(out / "daily_overview.csv",
               ["workflow_id", "date", "total_calls", "connected_calls", "connectivity_rate", "success_leads"],
               ([wf, d, v["total"], v["connected"], f"{v['connected'] / v['total']:.4f}", len(v["success"])]
                for (wf, d), v in sorted(r["daily"].items())))

    cols = ["workflow_id", "external_ref", "batch_id", "phone_masked", "bucket", "total_calls",
            "connected_calls", "last_call_at", "current_disposition", "ever_success"]
    _write_csv(out / "lead_report.csv", cols, ([row[c] for c in cols] for row in r["leads"]))

    _write_csv(out / "rejections.csv", ["workflow_id", "batch_id", "row_number", "reason"],
               conn.execute("SELECT workflow_id, batch_id, row_number, reason FROM rejections"
                            " ORDER BY workflow_id, batch_id, row_number, file_id"))

    (out / "data_quality.json").write_text(json.dumps(r["quality"], indent=2) + "\n", encoding="utf-8")
    return r["quality"]
