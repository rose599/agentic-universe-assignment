"""End-to-end: generated data, cross-checked against an independent reference implementation."""
import csv
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from solution.cli import main
from solution.db import connect
from solution.ingest import ingest_drop
from solution.report import write_reports
from solution.security import Vault

from .oracle import run_oracle

SEEDS = [42, 7, 2026]


def pipeline(tmp_path, cfg, drops, name="db", vault=None):
    conn = connect(str(tmp_path / f"{name}.sqlite"))
    vault = vault or Vault(b"test-secret-0123456789abcdef")
    for d in drops:
        ingest_drop(conn, vault, cfg, d)
    out = tmp_path / f"reports_{name}"
    write_reports(conn, cfg, out)
    return conn, out


def read(out, name):
    return list(csv.DictReader(open(out / name, newline="")))


@pytest.mark.parametrize("seed", SEEDS)
def test_matches_independent_reference_implementation(seed, make_data, cfg, tmp_path):
    data = make_data(seed)
    drops = [data / "drop_1", data / "drop_2"]
    _, out = pipeline(tmp_path, cfg, drops)
    ref = run_oracle(drops, cfg)

    assert json.load(open(out / "data_quality.json")) == {
        **{k: ref["quality"].get(k, 0) for k in (
            "lead_rows_total", "lead_rows_rejected", "duplicate_lead_rows_merged", "field_warnings", "events_total",
            "malformed_events", "duplicate_events_dropped", "orphan_events", "unknown_disposition_events")},
        "rejections_by_reason": ref["quality"]["rejections_by_reason"]}

    got = [{k: v for k, v in r.items()} for r in read(out, "lead_report.csv")]
    assert got == ref["leads"]

    bs = [(r["workflow_id"], r["batch_id"], int(r["total_leads"]), int(r["yet_to_reach_out"]),
           int(r["in_progress_telephony_failed"]), int(r["in_progress_dialed"]), int(r["completed"]))
          for r in read(out, "batch_summary.csv")]
    exp = [(wf, b, c["total_leads"], c["yet_to_reach_out"], c["in_progress_telephony_failed"],
            c["in_progress_dialed"], c["completed"]) for (wf, b), c in sorted(ref["batches"].items())]
    assert bs == exp

    do = [(r["workflow_id"], r["date"], int(r["total_calls"]), int(r["connected_calls"]),
           r["connectivity_rate"], int(r["success_leads"])) for r in read(out, "daily_overview.csv")]
    expd = [(wf, d, v[0], v[1], f"{v[1] / v[0]:.4f}", len(v[2])) for (wf, d), v in sorted(ref["daily"].items())]
    assert do == expd

    rej = [(r["workflow_id"], r["batch_id"], int(r["row_number"]), r["reason"]) for r in read(out, "rejections.csv")]
    assert rej == ref["rejections"]


@pytest.mark.parametrize("seed", SEEDS)
def test_reingest_changes_nothing(seed, make_data, cfg, tmp_path):
    data = make_data(seed)
    drops = [data / "drop_1", data / "drop_2"]
    conn, out1 = pipeline(tmp_path, cfg, drops)
    before = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("leads", "events", "rejections", "ingested_files")]
    vault = Vault(b"test-secret-0123456789abcdef")
    for d in drops * 2:                                  # run both drops twice more
        results = ingest_drop(conn, vault, cfg, d)
        assert all(r.status == "skipped_already_ingested" for r in results)
    after = [conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("leads", "events", "rejections", "ingested_files")]
    assert before == after
    out2 = tmp_path / "again"
    write_reports(conn, cfg, out2)
    for f in out1.iterdir():
        assert (out2 / f.name).read_bytes() == f.read_bytes(), f.name


@pytest.mark.parametrize("seed", SEEDS)
def test_incremental_equals_everything_at_once(seed, make_data, cfg, tmp_path):
    data = make_data(seed)
    everything = tmp_path / "all"
    for sub in ("drop_1", "drop_2"):
        shutil.copytree(data / sub, everything, dirs_exist_ok=True)   # one drop holding every file
    _, inc = pipeline(tmp_path, cfg, [data / "drop_1", data / "drop_2"], name="inc")
    _, one = pipeline(tmp_path, cfg, [everything], name="one")
    for f in inc.iterdir():
        assert (one / f.name).read_bytes() == f.read_bytes(), f.name


def test_output_contract_format(make_data, cfg, tmp_path):
    _, out = pipeline(tmp_path, cfg, [make_data(42) / "drop_1", make_data(42) / "drop_2"])
    assert sorted(p.name for p in out.iterdir()) == [
        "batch_summary.csv", "daily_overview.csv", "data_quality.json", "lead_report.csv", "rejections.csv"]
    assert open(out / "batch_summary.csv").readline().strip() == \
        "workflow_id,batch_id,total_leads,yet_to_reach_out,in_progress_telephony_failed,in_progress_dialed,completed"
    assert open(out / "daily_overview.csv").readline().strip() == \
        "workflow_id,date,total_calls,connected_calls,connectivity_rate,success_leads"
    assert open(out / "lead_report.csv").readline().strip() == \
        "workflow_id,external_ref,batch_id,phone_masked,bucket,total_calls,connected_calls,last_call_at,current_disposition,ever_success"
    assert open(out / "rejections.csv").readline().strip() == "workflow_id,batch_id,row_number,reason"
    assert set(json.load(open(out / "data_quality.json"))) == {
        "lead_rows_total", "lead_rows_rejected", "rejections_by_reason", "duplicate_lead_rows_merged", "field_warnings",
        "events_total", "malformed_events", "duplicate_events_dropped", "orphan_events", "unknown_disposition_events"}
    rows = read(out, "lead_report.csv")
    assert rows == sorted(rows, key=lambda r: (r["workflow_id"], r["external_ref"]))
    assert all(r["phone_masked"].startswith("+91******") and len(r["phone_masked"]) == 13 for r in rows)
    # bucket counts add up to total leads per batch
    for r in read(out, "batch_summary.csv"):
        assert int(r["total_leads"]) == sum(int(r[b]) for b in (
            "yet_to_reach_out", "in_progress_telephony_failed", "in_progress_dialed", "completed"))


def test_no_plaintext_pii_anywhere_in_database_file(make_data, cfg, tmp_path):
    data = make_data(42)
    conn, _ = pipeline(tmp_path, cfg, [data / "drop_1", data / "drop_2"])
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    blob = (tmp_path / "db.sqlite").read_bytes()
    names, phones = set(), set()
    for f in data.glob("*/leads/*/*.csv"):
        for row in csv.reader(open(f, encoding="utf-8")):
            for cell in row:
                c = cell.strip()
                if len(c.split()) == 2 and c.replace(" ", "").isalpha():
                    names.add(c)
                digits = "".join(ch for ch in c if ch.isdigit())
                if len(digits) >= 10 and all(ch in "+()- 0123456789" for ch in c):
                    phones.add(digits[-10:])
    assert len(names) > 100 and len(phones) > 100
    assert not [n for n in names if n.encode() in blob]
    assert not [p for p in phones if p.encode() in blob]


def test_cli_end_to_end(make_data, tmp_path, monkeypatch, capsys):
    data = make_data(42)
    monkeypatch.setenv("AU_PII_SECRET", "cli-test-secret-0123456789")
    db = str(tmp_path / "cli.sqlite")
    assert main(["--db", db, "ingest", str(data / "drop_1")]) == 0
    assert main(["--db", db, "ingest", str(data / "drop_2")]) == 0
    assert main(["--db", db, "report", "--out", str(tmp_path / "r")]) == 0
    assert (tmp_path / "r" / "lead_report.csv").exists()
    assert main(["--db", db, "ingest", str(data / "drop_1")]) == 0
    assert "skipped_already_ingested" in capsys.readouterr().out


def test_modified_file_is_not_silently_reingested(make_data, cfg, tmp_path):
    data = tmp_path / "d"
    shutil.copytree(make_data(42) / "drop_1", data)
    conn, _ = pipeline(tmp_path, cfg, [data])
    f = next(data.glob("events/*.jsonl"))
    f.write_text(f.read_text() + "\n")
    f.write_text(f.read_text().replace("call.ended", "call.ended", 1) + '{"event_id": "evt_new"}\n')
    results = ingest_drop(conn, Vault(b"test-secret-0123456789abcdef"), cfg, data)
    assert any(r.status == "skipped_changed" for r in results)


def test_ledger_is_append_only(conn):
    conn.execute("INSERT INTO ingested_files (id,kind,drop_name,rel_path,sha256,ingested_at) VALUES (1,'events','d','e','x','t')")
    conn.execute("INSERT INTO events (event_id, occurred_at, payload, file_id) VALUES ('e1','2026-01-01T00:00:00.000000Z','{}',1)")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE events SET call_id='x'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM events")
