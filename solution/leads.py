"""Lead-file ingestion: header mapping, validation, normalisation, rejection, merging."""
from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .normalize import normalize_header, normalize_phone, parse_date, parse_decimal
from .schemas import Field, Schema
from .security import Vault


@dataclass
class LeadStats:
    rows_total: int = 0
    rows_rejected: int = 0
    duplicates_merged: int = 0
    field_warnings: int = 0
    leads_created: int = 0


def parse_value(f: Field, raw: str):
    """Return (ok, value). `raw` is already trimmed and non-empty."""
    if f.type == "string":
        return True, raw
    if f.type == "phone":
        v = normalize_phone(raw)
        return v is not None, v
    if f.type == "date":
        d = parse_date(raw)
        return d is not None, (d.isoformat() if d else None)
    if f.type == "decimal":
        d = parse_decimal(raw)
        return d is not None, (str(d) if d is not None else None)
    if f.type == "enum":
        v = raw.lower()
        ok = v in {x.lower() for x in f.values}
        return ok, (v if ok else None)
    raise ValueError(f"unsupported field type {f.type!r}")


def validate_row(schema: Schema, record: dict[str, str]):
    """Walk fields in schema order. Returns (reject_reason | None, values, warnings)."""
    values: dict[str, str | None] = {}
    warnings = 0
    for f in schema.fields:
        raw = (record.get(f.name) or "").strip()
        if raw == "":
            if f.required:
                return f"missing_required:{f.name}", values, 0
            values[f.name] = None
            continue
        ok, val = parse_value(f, raw)
        if ok:
            values[f.name] = val
        elif f.type == "phone":
            return "invalid_phone", values, 0
        elif f.required:
            # Spec only defines two reasons; an unusable required value is "missing".
            return f"missing_required:{f.name}", values, 0
        else:
            values[f.name] = None
            warnings += 1
    return None, values, warnings


def read_rows(path: Path, schema: Schema):
    """Yield (row_number, {field_name: raw}) for non-blank data rows."""
    lookup = schema.header_lookup()
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return
        col_of: dict[str, int] = {}
        for i, h in enumerate(header):
            name = lookup.get(normalize_header(h))
            if name is not None:
                col_of.setdefault(name, i)  # first matching column wins
        n = 0
        for row in reader:
            if all(not c.strip() for c in row):
                continue  # blank rows are skipped and NOT counted
            n += 1
            yield n, {name: (row[i] if i < len(row) else "") for name, i in col_of.items()}


def ingest_lead_file(conn: sqlite3.Connection, vault: Vault, schema: Schema, path: Path,
                     file_id: int, batch_id: str) -> LeadStats:
    stats = LeadStats()
    phone_f = schema.phone_field
    pii = {f.name for f in schema.fields if f.pii}
    defaults = {f.name: f.default for f in schema.fields if f.default is not None}

    for row_number, record in read_rows(path, schema):
        stats.rows_total += 1
        reason, values, warnings = validate_row(schema, record)
        if reason:
            stats.rows_rejected += 1
            conn.execute(
                "INSERT INTO rejections (file_id, workflow_id, batch_id, row_number, reason)"
                " VALUES (?,?,?,?,?)", (file_id, schema.workflow_id, batch_id, row_number, reason))
            continue
        stats.field_warnings += warnings

        phone = values[phone_f.name]
        phash = vault.phone_hash(phone)
        existing = conn.execute(
            "SELECT id, fields FROM leads WHERE workflow_id=? AND phone_hash=?",
            (schema.workflow_id, phash)).fetchone()

        incoming = {k: (vault.encrypt(v) if k in pii else v)
                    for k, v in values.items() if v is not None}
        if existing is None:
            merged = {**{k: v for k, v in defaults.items()}, **incoming}
            conn.execute(
                "INSERT INTO leads (workflow_id, phone_hash, phone_last4, batch_id, source_file_id, fields)"
                " VALUES (?,?,?,?,?,?)",
                (schema.workflow_id, phash, phone[-4:], batch_id, file_id,
                 json.dumps(merged, sort_keys=True)))
            stats.leads_created += 1
        else:
            # last write wins, but only for fields where the new value is non-null.
            # Defaults were applied at creation and a later non-null value overrides them,
            # which is equivalent to "apply defaults after merging".
            current = json.loads(existing[1])
            current.update(incoming)
            conn.execute("UPDATE leads SET fields=? WHERE id=?",
                         (json.dumps(current, sort_keys=True), existing[0]))
            stats.duplicates_merged += 1
    return stats
