"""Deliberately naive, DB-free reference implementation used ONLY to cross-check the pipeline.

It re-derives every report straight from the raw files with plain dicts/lists. It shares
`normalize.py` (unit-tested separately) but none of the ingestion / linking / report code.
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from solution.normalize import IST, normalize_header, normalize_phone, parse_date, parse_decimal, parse_timestamp


def _parse(f, raw):
    if f.type == "string":
        return raw
    if f.type == "phone":
        return normalize_phone(raw)
    if f.type == "date":
        d = parse_date(raw)
        return d.isoformat() if d else None
    if f.type == "decimal":
        d = parse_decimal(raw)
        return str(d) if d is not None else None
    if f.type == "enum":
        return raw.lower() if raw.lower() in f.values else None


def run_oracle(drops, cfg):
    leads, order = {}, []
    rejections, q = [], defaultdict(int)
    reasons = defaultdict(int)

    for drop in drops:
        for wf_dir in sorted((Path(drop) / "leads").iterdir()):
            schema = cfg.schemas[wf_dir.name]
            for fpath in sorted(wf_dir.glob("*.csv")):
                batch = fpath.stem
                rows = list(csv.reader(open(fpath, newline="", encoding="utf-8-sig")))
                header = [normalize_header(h) for h in rows[0]]
                names = {}
                for f in schema.fields:
                    for label in (f.name, *f.aliases):
                        names.setdefault(normalize_header(label), f.name)
                idx = {}
                for i, h in enumerate(header):
                    if h in names and names[h] not in idx:
                        idx[names[h]] = i
                n = 0
                for row in rows[1:]:
                    if not any(c.strip() for c in row):
                        continue
                    n += 1
                    q["lead_rows_total"] += 1
                    rec = {k: (row[i].strip() if i < len(row) else "") for k, i in idx.items()}
                    vals, bad, warn = {}, None, 0
                    for f in schema.fields:
                        raw = rec.get(f.name, "")
                        if raw == "":
                            if f.required:
                                bad = f"missing_required:{f.name}"
                                break
                            vals[f.name] = None
                            continue
                        v = _parse(f, raw)
                        if v is None:
                            if f.type == "phone":
                                bad = "invalid_phone"
                                break
                            if f.required:
                                bad = f"missing_required:{f.name}"
                                break
                            warn += 1
                        vals[f.name] = v
                    if bad:
                        q["lead_rows_rejected"] += 1
                        reasons[bad] += 1
                        rejections.append((wf_dir.name, batch, n, bad))
                        continue
                    q["field_warnings"] += warn
                    key = (wf_dir.name, vals[schema.phone_field.name])
                    if key not in leads:
                        leads[key] = {"batch": batch, "vals": dict(vals)}
                        order.append(key)
                    else:
                        q["duplicate_lead_rows_merged"] += 1
                        for k, v in vals.items():
                            if v is not None:
                                leads[key]["vals"][k] = v
        # events are read after leads of the same drop, but linking is done at the end
    for key, L in leads.items():
        for f in cfg.schemas[key[0]].fields:
            if L["vals"].get(f.name) is None and f.default is not None:
                L["vals"][f.name] = f.default

    seen, events = set(), []
    for drop in drops:
        for fpath in sorted((Path(drop) / "events").glob("*.jsonl")):
            for line in open(fpath, encoding="utf-8"):
                if not line.strip():
                    continue
                q["events_total"] += 1
                try:
                    e = json.loads(line)
                    assert isinstance(e, dict) and e.get("event_id")
                except Exception:
                    q["malformed_events"] += 1
                    continue
                if e["event_id"] in seen:
                    q["duplicate_events_dropped"] += 1
                    continue
                seen.add(e["event_id"])
                e["_ts"] = parse_timestamp(e["occurred_at"])
                events.append(e)

    call_lead = {}
    linked = []
    for e in events:
        if e["event_type"] == "disposition.override":
            continue
        k = (e.get("workflow_id"), normalize_phone(e.get("phone")))
        if k in leads:
            call_lead.setdefault(e["call_id"], k)
            linked.append((k, e))
        else:
            q["orphan_events"] += 1
    for e in events:
        if e["event_type"] == "disposition.override":
            if e["call_id"] in call_lead:
                linked.append((call_lead[e["call_id"]], e))
            else:
                q["orphan_events"] += 1

    calls = defaultdict(lambda: {"init": [], "conn": False, "fail": False, "set": [], "ovr": []})
    for k, e in linked:
        c = calls[(k, e["call_id"])]
        t = e["event_type"]
        if t == "call.initiated":
            c["init"].append(e["_ts"])
        elif t == "call.connected":
            c["conn"] = True
        elif t == "call.telephony_failed":
            c["fail"] = True
        elif t.startswith("disposition."):
            code = e["payload"]["disposition_code"]
            if (k[0], code) not in cfg.dispositions:
                q["unknown_disposition_events"] += 1
                continue
            c["ovr" if t.endswith("override") else "set"].append((e["_ts"], e["event_id"], code))

    per_lead = defaultdict(list)
    for (k, cid), c in calls.items():
        if not c["init"]:
            continue
        pool = c["ovr"] or c["set"]
        c["eff"] = max(pool)[2] if pool else None
        c["t0"] = min(c["init"])
        c["cid"] = cid
        per_lead[k].append(c)

    out_leads, batches, daily = [], defaultdict(lambda: defaultdict(int)), defaultdict(lambda: [0, 0, set()])
    for k in order:
        wf = k[0]
        L = leads[k]
        cs = sorted(per_lead.get(k, []), key=lambda c: (c["t0"], c["cid"]))
        d = lambda c: cfg.dispositions.get((wf, c["eff"]))
        if not cs:
            bucket = "yet_to_reach_out"
        elif any(d(c) and d(c).is_terminal for c in cs):
            bucket = "completed"
        elif all(c["fail"] for c in cs):
            bucket = "in_progress_telephony_failed"
        else:
            bucket = "in_progress_dialed"
        cur = [c["eff"] for c in cs if c["eff"]]
        ref = cfg.schemas[wf].reference_field.name
        out_leads.append({
            "workflow_id": wf, "external_ref": L["vals"][ref], "batch_id": L["batch"],
            "phone_masked": "+91******" + k[1][-4:], "bucket": bucket, "total_calls": str(len(cs)),
            "connected_calls": str(sum(c["conn"] for c in cs)),
            "last_call_at": cs[-1]["t0"].astimezone(IST).isoformat(timespec="seconds") if cs else "",
            "current_disposition": cur[-1] if cur else "",
            "ever_success": "true" if any(d(c) and d(c).is_success for c in cs) else "false"})
        b = batches[(wf, L["batch"])]
        b["total_leads"] += 1
        b[bucket] += 1
        for c in cs:
            day = daily[(wf, c["t0"].astimezone(IST).date().isoformat())]
            day[0] += 1
            day[1] += c["conn"]
            if d(c) and d(c).is_success:
                day[2].add(k)
    out_leads.sort(key=lambda r: (r["workflow_id"], r["external_ref"]))

    q["rejections_by_reason"] = dict(sorted(reasons.items()))
    return {"leads": out_leads, "batches": batches, "daily": daily, "quality": dict(q),
            "rejections": sorted(rejections)}
