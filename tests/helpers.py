"""Tiny builders for hand-made drops used in logic tests."""
import csv
import json
from pathlib import Path

PHONE = "9876543210"
HDR = ["customer_name", "phone", "policy_number"]


def make_drop(root: Path, name: str, leads=None, events=None, batch="batch_001", wf="motor_renewal") -> Path:
    drop = root / name
    if leads is not None:
        d = drop / "leads" / wf
        d.mkdir(parents=True)
        with open(d / f"{batch}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(HDR)
            w.writerows(leads)
    if events is not None:
        d = drop / "events"
        d.mkdir(parents=True)
        (d / "events_0001.jsonl").write_text("\n".join(e if isinstance(e, str) else json.dumps(e) for e in events) + "\n")
    return drop


_n = [0]


def ev(etype, call="c1", at="2026-10-01T10:00:00Z", phone=PHONE, wf="motor_renewal", payload=None, eid=None):
    _n[0] += 1
    e = {"event_id": eid or f"e{_n[0]:06d}", "event_type": etype, "workflow_id": wf, "call_id": call,
         "occurred_at": at, "payload": payload or {}}
    if phone is not None:
        e["phone"] = phone
    return e


def disp(code, call="c1", at="2026-10-01T10:10:00Z", override=False, **kw):
    t = "disposition.override" if override else "disposition.set"
    return ev(t, call=call, at=at, payload={"disposition_code": code}, phone=None if override else kw.pop("phone", PHONE), **kw)
