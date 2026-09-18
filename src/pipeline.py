"""Pipeline orchestrator: rebuild the harmonised database from raw files.

Usage:
    python -m src.pipeline            # full rebuild
    python -m src.pipeline --db output/harmonised.duckdb
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from . import classify as classify_mod
from . import database, harmonize as harmonize_mod
from .ingest import ingest_country
from .quality import check_dataset

PIPELINE_VERSION = "0.1.0"

# Issue types that push a record into the analyst review queue even when a
# rule technically resolved it (e.g. adversarial text classified via CoA).
REVIEW_TRIGGER_ISSUES = {"adversarial_text", "description_coa_mismatch"}
REVIEW_TRIGGER_SEVERITY = {"error"}


def _issue_id(batch_id: str, record_id, issue_type: str, observed) -> str:
    key = f"{batch_id}|{record_id}|{issue_type}|{observed}"
    return "I" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:15]


def run(config_path: Path = Path("config/countries.yaml"),
        rules_path: Path = Path("config/classification_rules.yaml"),
        db_path: Path | None = None) -> dict:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    rules = classify_mod.load_rules(rules_path)
    raw_dir = Path(cfg["raw_dir"])
    ref_dir = Path(cfg["ref_dir"])
    db_path = db_path or Path(cfg["db_path"])
    batch_id = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"

    # --- 1-2. ingest + harmonise -----------------------------------------
    all_records, all_issues, coa_rows, source_files = [], [], [], []
    for country_code, ccfg in cfg["countries"].items():
        res = ingest_country(country_code, ccfg, raw_dir)
        all_records.extend(res.records)
        all_issues.extend((country_code, iss) for iss in res.issues)
        coa_rows.extend(res.coa_entries)
        source_files.append({
            "batch_id": batch_id, "country_code": country_code,
            "file_name": res.file_name, "format": ccfg["format"],
            "rows_read": res.rows_read, "rows_loaded": len(res.records),
            "file_checks": json.dumps(res.file_checks),
        })
        print(f"[{country_code}] {res.file_name}: {res.rows_read} source rows, "
              f"{len(res.records)} records, {len(res.issues)} ingest issues")

    tx = harmonize_mod.harmonize(all_records, batch_id)
    loc_to_rid = dict(zip(tx["source_row_or_path"], tx["record_id"]))

    # --- 3. data-quality issues -------------------------------------------
    dq_rows = []
    for country_code, iss in all_issues:  # ingest-level issues
        rid = loc_to_rid.get(iss.source_row_or_path) if iss.source_row_or_path else None
        dq_rows.append({
            "issue_id": _issue_id(batch_id, rid or iss.source_row_or_path,
                                  iss.issue_type, iss.observed_value),
            "record_id": rid, "batch_id": batch_id,
            "country_code": country_code,
            "issue_type": iss.issue_type, "severity": iss.severity,
            "field_name": iss.field_name,
            "observed_value": None if iss.observed_value is None else str(iss.observed_value)[:500],
            "detail": iss.detail, "status": "open",
        })

    for iss in check_dataset(tx):  # dataset-level issues
        iss.update({
            "issue_id": _issue_id(batch_id, iss["record_id"], iss["issue_type"],
                                  iss["observed_value"]),
            "batch_id": batch_id,
        })
        dq_rows.append(iss)
    dq = pd.DataFrame(dq_rows)

    # --- 4. classify -------------------------------------------------------
    flagged = set(
        dq.loc[dq["issue_type"].isin(REVIEW_TRIGGER_ISSUES)
               | dq["severity"].isin(REVIEW_TRIGGER_SEVERITY), "record_id"].dropna()
    )
    cls = classify_mod.classify(tx, rules, flagged_ids=flagged)

    # --- 5. reference tables ------------------------------------------------
    ref_country = pd.read_csv(ref_dir / "ref_countries.csv")
    ref_sha = pd.read_csv(ref_dir / "ref_sha_classification.csv")[["sha_code", "sha_description"]]
    ref_srhr = pd.read_csv(ref_dir / "ref_srhr_classification.csv")[["srhr_code", "srhr_description"]]
    ref_coa = pd.DataFrame(coa_rows)

    # --- 6. persist ----------------------------------------------------------
    con = database.init_db(db_path)
    database.reset_batch(con)
    con.execute(
        "INSERT INTO ingestion_batch VALUES (?, ?, ?, ?, ?)",
        [batch_id, datetime.now(), PIPELINE_VERSION, "completed",
         f"{len(tx)} records from {len(source_files)} files"],
    )
    database.insert_df(con, "source_file", pd.DataFrame(source_files))
    database.insert_df(con, "transactions", tx)
    database.insert_df(con, "classifications", cls)
    database.insert_df(con, "dq_issues", dq)
    database.insert_df(con, "ref_country", ref_country)
    database.insert_df(con, "ref_coa", ref_coa)
    database.insert_df(con, "ref_sha", ref_sha)
    database.insert_df(con, "ref_srhr", ref_srhr)
    con.close()

    # --- summary --------------------------------------------------------------
    stats = {
        "batch_id": batch_id,
        "records": len(tx),
        "dq_issues": len(dq),
        "needs_review": int((cls["review_status"] == "needs_review").sum()),
        "sha_mapped": int(cls["sha_code"].notna().sum()),
        "srhr_mapped": int(cls["srhr_code"].notna().sum()),
        "db_path": str(db_path),
    }
    print(f"\nbatch {batch_id}")
    print(f"  harmonised records : {stats['records']} "
          f"({int((tx['record_role'] == 'parent').sum())} parents, "
          f"{int((tx['record_role'] == 'child').sum())} children)")
    print(f"  SHA mapped         : {stats['sha_mapped']}  |  SRHR mapped: {stats['srhr_mapped']}")
    print(f"  review queue       : {stats['needs_review']}")
    print(f"  dq issues          : {stats['dq_issues']}")
    print(f"  database           : {db_path}")
    return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rebuild the harmonised expenditure database.")
    ap.add_argument("--config", default="config/countries.yaml")
    ap.add_argument("--rules", default="config/classification_rules.yaml")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    run(Path(args.config), Path(args.rules), Path(args.db) if args.db else None)
