import csv
import json

from solution.leads import ingest_lead_file, read_rows, validate_row


def write_csv(path, header, rows, newline="\r\n"):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator=newline)
        w.writerow(header)
        w.writerows(rows)


def test_rejection_reason_is_first_failure_in_schema_order(cfg):
    s = cfg.schemas["motor_renewal"]
    # name missing AND phone invalid -> name comes first in schema order
    r, *_ = validate_row(s, {"customer_name": "", "phone": "123", "policy_number": "P1"})
    assert r == "missing_required:customer_name"
    r, *_ = validate_row(s, {"customer_name": "A", "phone": "123", "policy_number": ""})
    assert r == "invalid_phone"  # phone is before policy_number
    r, *_ = validate_row(s, {"customer_name": "A", "phone": "", "policy_number": "P1"})
    assert r == "missing_required:phone"
    r, *_ = validate_row(s, {"customer_name": "   ", "phone": "9876543210", "policy_number": "P1"})
    assert r == "missing_required:customer_name"  # whitespace == missing


def test_optional_invalid_becomes_null_with_one_warning_each(cfg):
    s = cfg.schemas["motor_renewal"]
    r, vals, warns = validate_row(s, {"customer_name": "A", "phone": "9876543210", "policy_number": "P1",
                                      "policy_expiry": "31/02/2026", "premium_amount": "N/A",
                                      "preferred_language": "Hindi"})
    assert r is None and warns == 3
    assert vals["policy_expiry"] is None and vals["premium_amount"] is None and vals["preferred_language"] is None


def test_empty_optional_is_missing_not_a_warning(cfg):
    s = cfg.schemas["motor_renewal"]
    r, vals, warns = validate_row(s, {"customer_name": "A", "phone": "9876543210", "policy_number": "P1",
                                      "preferred_language": "   "})
    assert r is None and warns == 0


def test_rejected_rows_contribute_no_warnings(cfg):
    s = cfg.schemas["motor_renewal"]
    r, _, warns = validate_row(s, {"customer_name": "A", "phone": "bad", "policy_number": "P1",
                                   "premium_amount": "N/A"})
    assert r == "invalid_phone" and warns == 0


def test_enum_is_case_insensitive_and_stored_lowercase(cfg):
    s = cfg.schemas["motor_renewal"]
    _, vals, _ = validate_row(s, {"customer_name": "A", "phone": "9876543210", "policy_number": "P1",
                                  "preferred_language": " HI "})
    assert vals["preferred_language"] == "hi"


def test_headers_aliases_extra_columns_blank_rows_and_row_numbers(cfg, tmp_path):
    p = tmp_path / "batch_009.csv"
    write_csv(p, ["Language", "Insured_Name", "POLICY  NO", "Mobile No", "agent_notes"], [
        ["en", "Asha", "P1", "9876543210", "vip"],
        ["", "", "", "", ""],                                  # blank row: skipped, not counted
        ["hi", "Bela", "P2", "123", ""],                       # data row 2 -> rejected
        ["   ", " ", "", "", ""],                              # whitespace-only: blank
        ["ta", "Chitra", "", "9876543211", ""],                # data row 3 -> missing policy
    ])
    rows = list(read_rows(p, cfg.schemas["motor_renewal"]))
    assert [n for n, _ in rows] == [1, 2, 3]
    assert rows[0][1]["customer_name"] == "Asha" and rows[0][1]["policy_number"] == "P1"
    assert "agent_notes" not in rows[0][1]


def test_ingest_merge_rules_and_batch_id_is_permanent(cfg, conn, vault, tmp_path):
    s = cfg.schemas["motor_renewal"]
    hdr = ["customer_name", "phone", "policy_number", "policy_expiry", "premium_amount", "preferred_language"]
    a, b = tmp_path / "batch_001.csv", tmp_path / "batch_002.csv"
    write_csv(a, hdr, [["Asha", "+91 98765 43210", "P1", "2026-12-01", "₹1,000", ""]])
    write_csv(b, hdr, [["Asha K", "09876543210", "P1-new", "", "Rs 2000", "HI"]])  # same identity
    conn.execute("INSERT INTO ingested_files (id,kind,drop_name,rel_path,sha256,ingested_at) VALUES (1,'leads','d','a','x','t')")
    conn.execute("INSERT INTO ingested_files (id,kind,drop_name,rel_path,sha256,ingested_at) VALUES (2,'leads','d','b','y','t')")
    s1 = ingest_lead_file(conn, vault, s, a, 1, "batch_001")
    s2 = ingest_lead_file(conn, vault, s, b, 2, "batch_002")
    assert (s1.leads_created, s1.duplicates_merged, s2.leads_created, s2.duplicates_merged) == (1, 0, 0, 1)

    batch, fields = conn.execute("SELECT batch_id, fields FROM leads").fetchone()
    f = json.loads(fields)
    assert batch == "batch_001"                      # first valid row decides, forever
    assert vault.decrypt(f["customer_name"]) == "Asha K"   # overwritten (non-null)
    assert f["policy_number"] == "P1-new"
    assert f["policy_expiry"] == "2026-12-01"        # later row was empty -> kept
    assert f["premium_amount"] == "2000.00"
    assert f["preferred_language"] == "hi"           # default 'en' replaced by later non-null


def test_default_applies_when_still_null_after_merge(cfg, conn, vault, tmp_path):
    s = cfg.schemas["motor_renewal"]
    hdr = ["customer_name", "phone", "policy_number", "preferred_language"]
    a = tmp_path / "batch_001.csv"
    write_csv(a, hdr, [["Asha", "9876543210", "P1", ""], ["Asha", "9876543210", "P1", "klingon"]])
    conn.execute("INSERT INTO ingested_files (id,kind,drop_name,rel_path,sha256,ingested_at) VALUES (1,'leads','d','a','x','t')")
    st = ingest_lead_file(conn, vault, s, a, 1, "batch_001")
    f = json.loads(conn.execute("SELECT fields FROM leads").fetchone()[0])
    assert f["preferred_language"] == "en" and st.field_warnings == 1


def test_pii_not_stored_in_plaintext(cfg, conn, vault, tmp_path):
    s = cfg.schemas["motor_renewal"]
    a = tmp_path / "batch_001.csv"
    write_csv(a, ["customer_name", "phone", "policy_number"], [["Zebulon Quux", "9876543210", "P1"]])
    conn.execute("INSERT INTO ingested_files (id,kind,drop_name,rel_path,sha256,ingested_at) VALUES (1,'leads','d','a','x','t')")
    ingest_lead_file(conn, vault, s, a, 1, "batch_001")
    dump = "\n".join(conn.iterdump())
    assert "Zebulon" not in dump and "9876543210" not in dump
