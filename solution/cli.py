"""Command line interface:

    python -m solution ingest data/drop_1
    python -m solution ingest data/drop_2
    python -m solution report --out reports/
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .db import connect
from .ingest import ingest_drop
from .report import write_reports
from .schemas import load_config
from .security import load_vault

DEFAULT_SCHEMAS = Path(__file__).resolve().parent.parent / "assignment-a-data" / "schemas"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m solution", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="ledger.sqlite", help="SQLite database file (default: ledger.sqlite)")
    p.add_argument("--schemas", default=str(DEFAULT_SCHEMAS), help="directory with workflow schemas + dispositions.csv")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("ingest", help="ingest one drop directory (safe to re-run)")
    pi.add_argument("drop_dir")
    pr = sub.add_parser("report", help="write the five report files")
    pr.add_argument("--out", default="reports")
    args = p.parse_args(argv)

    cfg = load_config(args.schemas)
    conn = connect(args.db)
    t0 = time.perf_counter()
    if args.cmd == "ingest":
        vault = load_vault(args.db)
        for res in ingest_drop(conn, vault, cfg, args.drop_dir):
            print(f"{res.status:26} {res.rel_path}  {res.detail}".rstrip())
    else:
        q = write_reports(conn, cfg, args.out)
        print(f"wrote reports to {args.out}/  (orphan events: {q['orphan_events']})")
    print(f"done in {time.perf_counter() - t0:.2f}s", file=sys.stderr)
    return 0
