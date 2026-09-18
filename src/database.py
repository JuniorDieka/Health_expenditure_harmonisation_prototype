"""DuckDB persistence layer.

Owns the schema and all writes. The database is rebuilt from raw files on
every pipeline run, so it can be treated as disposable; raw files are the
system of record.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

DDL = """
CREATE TABLE IF NOT EXISTS ingestion_batch (
    batch_id        VARCHAR PRIMARY KEY,
    run_at          TIMESTAMP,
    pipeline_version VARCHAR,
    status          VARCHAR,
    notes           VARCHAR
);

CREATE TABLE IF NOT EXISTS source_file (
    batch_id        VARCHAR,
    country_code    VARCHAR,
    file_name       VARCHAR,
    format          VARCHAR,
    rows_read       INTEGER,
    rows_loaded     INTEGER,
    file_checks     VARCHAR   -- JSON: reconciliation results
);

CREATE TABLE IF NOT EXISTS transactions (
    record_id            VARCHAR PRIMARY KEY,
    batch_id             VARCHAR,
    country_code         VARCHAR,
    source_file          VARCHAR,
    source_row_or_path   VARCHAR,
    source_transaction_id VARCHAR,
    transaction_date     DATE,
    fiscal_year          VARCHAR,
    ministry_code        VARCHAR,
    ministry_name        VARCHAR,
    account_code         VARCHAR,
    account_label        VARCHAR,
    description          VARCHAR,
    description_sanitized VARCHAR,
    supplier             VARCHAR,
    amount               DOUBLE,
    raw_amount_text      VARCHAR,
    currency             VARCHAR,
    parent_transaction_id VARCHAR,
    record_role          VARCHAR,
    include_in_totals    BOOLEAN,
    raw_payload          VARCHAR,
    record_hash          VARCHAR
);

CREATE TABLE IF NOT EXISTS classifications (
    record_id        VARCHAR PRIMARY KEY,
    sha_code         VARCHAR,
    sha_method       VARCHAR,
    sha_rule_id      VARCHAR,
    sha_confidence   DOUBLE,
    srhr_code        VARCHAR,
    srhr_method      VARCHAR,
    srhr_rule_id     VARCHAR,
    srhr_confidence  DOUBLE,
    rule_version     VARCHAR,
    review_status    VARCHAR,
    rationale        VARCHAR
);

CREATE TABLE IF NOT EXISTS dq_issues (
    issue_id         VARCHAR PRIMARY KEY,
    record_id        VARCHAR,
    batch_id         VARCHAR,
    country_code     VARCHAR,
    issue_type       VARCHAR,
    severity         VARCHAR,
    field_name       VARCHAR,
    observed_value   VARCHAR,
    detail           VARCHAR,
    status           VARCHAR
);

CREATE TABLE IF NOT EXISTS ref_country (
    country_code     VARCHAR PRIMARY KEY,
    country_name     VARCHAR,
    primary_currency VARCHAR,
    language         VARCHAR
);

CREATE TABLE IF NOT EXISTS ref_coa (
    country_code     VARCHAR,
    account_code     VARCHAR,
    account_label    VARCHAR,
    source           VARCHAR,
    PRIMARY KEY (country_code, account_code)
);

CREATE TABLE IF NOT EXISTS ref_sha (
    sha_code         VARCHAR PRIMARY KEY,
    sha_description  VARCHAR
);

CREATE TABLE IF NOT EXISTS ref_srhr (
    srhr_code        VARCHAR PRIMARY KEY,
    srhr_description VARCHAR
);
"""


def init_db(db_path: Path) -> duckdb.DuckDBPyConnection:
    """Create (or open) the database file and ensure the schema exists."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    con.execute(DDL)
    return con


def reset_batch(con: duckdb.DuckDBPyConnection) -> None:
    """Clear all loaded tables so a run is a clean rebuild."""
    for table in ("transactions", "classifications", "dq_issues",
                  "source_file", "ingestion_batch", "ref_country",
                  "ref_coa", "ref_sha", "ref_srhr"):
        con.execute(f"DELETE FROM {table}")


def insert_df(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    """Bulk-insert a dataframe into a table via a registered relation."""
    if df.empty:
        return 0
    con.register("_tmp_df", df)
    cols = ", ".join(df.columns)
    con.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM _tmp_df")
    con.unregister("_tmp_df")
    return len(df)
