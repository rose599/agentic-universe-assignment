import csv
import json

import pytest

from solution.ingest import ingest_drop
from solution.report import write_reports

from .helpers import PHONE, disp, ev, make_drop

LEAD = [["Asha", PHONE, "POL-1"]]


def run(tmp_path, conn, vault, cfg, *drops):
    for d in drops:
        ingest_drop(conn, vault, cfg, d)
    out = tmp_path / "out"
    q = write_reports(conn, cfg, out)
    rd = lambda n: list(csv.DictReader(open(out / n, newline="")))
    return rd("lead_report.csv"), rd("daily_overview.csv"), rd("batch_summary.csv"), q


def one_lead(tmp_path, conn, vault, cfg, events):
    d = make_drop(tmp_path, "d1", LEAD, events)
    leads, daily, batches, q = run(tmp_path, conn, vault, cfg, d)
    return leads[0], daily, batches, q


def test_lead_with_no_calls_is_yet_to_reach_out(tmp_path, conn, vault, cfg):
    lead, daily, batches, _ = one_lead(tmp_path, conn, vault, cfg, [])
    assert lead["bucket"] == "yet_to_reach_out" and lead["total_calls"] == "0"
    assert lead["last_call_at"] == "" and lead["current_disposition"] == "" and lead["ever_success"] == "false"
    assert daily == [] and batches[0]["yet_to_reach_out"] == "1"


def test_override_wins_over_set_and_original_is_kept_in_ledger(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated"), ev("call.connected", at="2026-10-01T10:00:05Z"),
              disp("not_interested", at="2026-10-01T10:05:00Z"),
              disp("renewed", at="2026-10-01T09:00:00Z", override=True)]   # override is *earlier* in time
    lead, *_ = one_lead(tmp_path, conn, vault, cfg, events)
    assert lead["current_disposition"] == "renewed" and lead["ever_success"] == "true"
    assert lead["bucket"] == "completed"
    # append-only: both disposition events are still in the ledger, untouched
    codes = [r[0] for r in conn.execute("SELECT disposition_code FROM events WHERE disposition_code IS NOT NULL ORDER BY seq")]
    assert sorted(codes) == ["not_interested", "renewed"]


def test_latest_override_wins_and_latest_set_wins_without_override(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated"), ev("call.connected"),
              disp("callback_requested", at="2026-10-01T10:01:00Z"),
              disp("renewed", at="2026-10-01T10:02:00Z"),                       # later set wins
              disp("dnd", at="2026-10-02T00:00:00Z", override=True),
              disp("payment_link_sent", at="2026-10-03T00:00:00Z", override=True)]  # latest override
    lead, *_ = one_lead(tmp_path, conn, vault, cfg, events)
    assert lead["current_disposition"] == "payment_link_sent"
    assert lead["bucket"] == "in_progress_dialed"      # non-terminal
    assert lead["ever_success"] == "false"             # 'renewed' was overridden


def test_override_can_turn_success_into_non_success(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated"), ev("call.connected"), disp("renewed"),
              disp("not_interested", at="2026-10-02T00:00:00Z", override=True)]
    lead, daily, *_ = one_lead(tmp_path, conn, vault, cfg, events)
    assert lead["ever_success"] == "false" and daily[0]["success_leads"] == "0"
    assert lead["bucket"] == "completed"               # not_interested is terminal


def test_bucket_telephony_failed_requires_every_call_failed(tmp_path, conn, vault, cfg):
    only_failed = [ev("call.initiated", "c1"), ev("call.telephony_failed", "c1"),
                   ev("call.initiated", "c2", at="2026-10-02T10:00:00Z"), ev("call.telephony_failed", "c2", at="2026-10-02T10:00:03Z")]
    lead, *_ = one_lead(tmp_path, conn, vault, cfg, only_failed)
    assert lead["bucket"] == "in_progress_telephony_failed" and lead["connected_calls"] == "0"


def test_bucket_mixed_failed_and_dialed_is_in_progress_dialed(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated", "c1"), ev("call.telephony_failed", "c1"),
              ev("call.initiated", "c2", at="2026-10-02T10:00:00Z"), ev("call.ended", "c2", at="2026-10-02T10:00:30Z",
                                                                         payload={"duration_sec": 0, "end_reason": "no_answer"})]
    lead, *_ = one_lead(tmp_path, conn, vault, cfg, events)
    assert lead["bucket"] == "in_progress_dialed"


def test_completed_beats_telephony_failed(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated", "c1"), ev("call.telephony_failed", "c1"),
              ev("call.initiated", "c2", at="2026-10-02T10:00:00Z"), ev("call.connected", "c2", at="2026-10-02T10:00:05Z"),
              disp("wrong_number", "c2", at="2026-10-02T10:03:00Z")]
    lead, *_ = one_lead(tmp_path, conn, vault, cfg, events)
    assert lead["bucket"] == "completed"


def test_call_without_initiated_is_not_counted(tmp_path, conn, vault, cfg):
    events = [ev("call.connected", "c9"), disp("renewed", "c9")]   # no call.initiated
    lead, daily, _, q = one_lead(tmp_path, conn, vault, cfg, events)
    assert lead["total_calls"] == "0" and lead["bucket"] == "yet_to_reach_out" and lead["ever_success"] == "false"
    assert daily == [] and q["orphan_events"] == 0     # linked, just not a counted call


def test_unknown_disposition_is_counted_and_ignored(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated"), ev("call.connected"), disp("renewed", at="2026-10-01T10:01:00Z"),
              disp("RENEWD", at="2026-10-01T10:05:00Z")]           # later, but unknown -> ignored
    lead, _, _, q = one_lead(tmp_path, conn, vault, cfg, events)
    assert q["unknown_disposition_events"] == 1 and lead["current_disposition"] == "renewed"


def test_disposition_is_checked_against_its_own_workflow(tmp_path, conn, vault, cfg):
    # 'refused' exists only for policy_feedback, not motor_renewal
    events = [ev("call.initiated"), ev("call.connected"), disp("refused")]
    lead, _, _, q = one_lead(tmp_path, conn, vault, cfg, events)
    assert q["unknown_disposition_events"] == 1 and lead["current_disposition"] == ""


def test_daily_overview_ist_dates_and_rates(tmp_path, conn, vault, cfg):
    events = [
        # 2026-10-01T19:00Z == 2026-10-02 00:30 IST  -> belongs to 10-02
        ev("call.initiated", "c1", at="2026-10-01T19:00:00Z"), ev("call.connected", "c1", at="2026-10-01T19:00:05Z"),
        disp("renewed", "c1", at="2026-10-01T19:05:00Z"),
        # naive timestamp == IST: 23:59:59 IST on 10-01
        ev("call.initiated", "c2", at="2026-10-01 23:59:59"),
        ev("call.initiated", "c3", at="2026-10-01T23:59:59+05:30"), ev("call.telephony_failed", "c3", at="2026-10-02T00:00:02+05:30"),
    ]
    _, daily, *_ = one_lead(tmp_path, conn, vault, cfg, events)
    assert [(r["date"], r["total_calls"], r["connected_calls"], r["connectivity_rate"], r["success_leads"]) for r in daily] == [
        ("2026-10-01", "2", "0", "0.0000", "0"), ("2026-10-02", "1", "1", "1.0000", "1")]


def test_success_leads_counts_distinct_leads_per_day(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated", "c1", at="2026-10-01T05:00:00Z"), ev("call.connected", "c1"), disp("renewed", "c1"),
              ev("call.initiated", "c2", at="2026-10-01T06:00:00Z"), ev("call.connected", "c2", at="2026-10-01T06:00:05Z"),
              disp("renewed", "c2", at="2026-10-01T06:05:00Z")]
    _, daily, *_ = one_lead(tmp_path, conn, vault, cfg, events)
    assert daily[0]["total_calls"] == "2" and daily[0]["success_leads"] == "1"


def test_last_call_at_is_ist_iso(tmp_path, conn, vault, cfg):
    lead, *_ = one_lead(tmp_path, conn, vault, cfg, [ev("call.initiated", at="2026-10-14T12:25:24Z")])
    assert lead["last_call_at"] == "2026-10-14T17:55:24+05:30"
    assert lead["phone_masked"] == "+91******3210"


def test_current_disposition_is_from_most_recent_call_that_has_one(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated", "c1"), ev("call.connected", "c1"), disp("callback_requested", "c1"),
              ev("call.initiated", "c2", at="2026-10-02T10:00:00Z")]      # newer call, no disposition
    lead, *_ = one_lead(tmp_path, conn, vault, cfg, events)
    assert lead["current_disposition"] == "callback_requested" and lead["total_calls"] == "2"


def test_malformed_duplicate_and_orphan_counts(tmp_path, conn, vault, cfg):
    e1 = ev("call.initiated", eid="dup1")
    events = [e1, e1, '{"event_id": "trunc', "not json at all", '{"no_event_id": 1}', "[1,2]", "",
              ev("call.initiated", "cx", phone="9123400000"),               # never uploaded -> orphan
              ev("call.initiated", "cy", phone="12345"),                    # unusable phone -> orphan
              disp("renewed", "ghost", override=True)]                       # override of unknown call -> orphan
    _, _, _, q = one_lead(tmp_path, conn, vault, cfg, events)
    assert q["events_total"] == 9                    # blank line not counted
    assert q["malformed_events"] == 4 and q["duplicate_events_dropped"] == 1 and q["orphan_events"] == 3


def test_event_linking_is_per_workflow(tmp_path, conn, vault, cfg):
    events = [ev("call.initiated", wf="policy_feedback")]        # same phone, other workflow, no such lead
    lead, _, _, q = one_lead(tmp_path, conn, vault, cfg, events)
    assert lead["total_calls"] == "0" and q["orphan_events"] == 1


def test_events_arriving_before_their_lead_count_once_lead_arrives(tmp_path, conn, vault, cfg):
    d1 = make_drop(tmp_path, "drop_1", None, [ev("call.initiated"), ev("call.connected", at="2026-10-01T10:00:05Z"),
                                                disp("renewed", at="2026-10-01T10:05:00Z")])
    d2 = make_drop(tmp_path, "drop_2", LEAD, None)
    ingest_drop(conn, vault, cfg, d1)
    q1 = write_reports(conn, cfg, tmp_path / "after1")
    assert q1["orphan_events"] == 3                                 # all orphans after drop_1
    leads, _, _, q = run(tmp_path, conn, vault, cfg, d2)
    assert q["orphan_events"] == 0 and leads[0]["bucket"] == "completed" and leads[0]["ever_success"] == "true"


def test_rejected_lead_events_stay_orphans(tmp_path, conn, vault, cfg):
    d = make_drop(tmp_path, "d", [["", PHONE, "POL-1"]], [ev("call.initiated")])        # no name -> rejected
    leads, _, _, q = run(tmp_path, conn, vault, cfg, d)
    assert leads == [] and q["lead_rows_rejected"] == 1 and q["orphan_events"] == 1
    assert q["rejections_by_reason"] == {"missing_required:customer_name": 1}


def test_rejections_csv_has_no_pii(tmp_path, conn, vault, cfg):
    d = make_drop(tmp_path, "d", [["", PHONE, "POL-1"], ["Zed", "bad", "POL-2"]], [])
    run(tmp_path, conn, vault, cfg, d)
    text = (tmp_path / "out" / "rejections.csv").read_text()
    assert text.splitlines() == ["workflow_id,batch_id,row_number,reason",
                                 "motor_renewal,batch_001,1,missing_required:customer_name",
                                 "motor_renewal,batch_001,2,invalid_phone"]
    assert "Zed" not in text and PHONE not in text
