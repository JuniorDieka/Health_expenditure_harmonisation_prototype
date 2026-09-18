"""Analyst review interface — run with:  streamlit run app.py

Read-mostly UI over the harmonised DuckDB database: overview KPIs,
data-quality summary, filterable transaction table, classification
review queue, and per-record lineage detail.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st
import yaml

st.set_page_config(page_title="Health Expenditure Harmonisation", layout="wide")

CFG = yaml.safe_load(Path("config/countries.yaml").read_text(encoding="utf-8"))
DB_PATH = CFG["db_path"]


@st.cache_resource
def get_con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=False)


@st.cache_data(ttl=60)
def q(sql: str, params: list | None = None) -> pd.DataFrame:
    con = get_con()
    return con.execute(sql, params or []).df()


VIEW_SQL = """
    SELECT t.record_id, t.country_code, t.source_transaction_id,
           t.transaction_date, t.fiscal_year, t.ministry_code, t.ministry_name,
           t.account_code, t.account_label, t.description, t.supplier,
           t.amount, t.currency, t.record_role, t.include_in_totals,
           c.sha_code, c.srhr_code, c.sha_method, c.srhr_method,
           c.sha_confidence, c.srhr_confidence, c.review_status, c.rationale
    FROM transactions t
    LEFT JOIN classifications c USING (record_id)
"""

st.title("Health Expenditure Harmonisation — Analyst Review")

if not Path(DB_PATH).exists():
    st.error(f"Database not found at `{DB_PATH}`. Run `python -m src.pipeline` first.")
    st.stop()

# ---------------------------------------------------------------- sidebar
countries = sorted(q("SELECT DISTINCT country_code FROM transactions")["country_code"])
f_country = st.sidebar.multiselect("Country", countries, default=countries)

base = q(f"{VIEW_SQL} WHERE t.country_code IN (SELECT UNNEST($1))", [f_country])

def _opts(col: str) -> list:
    return sorted(x for x in base[col].dropna().unique())

f_ministry = st.sidebar.multiselect("Ministry", _opts("ministry_code"))
f_account = st.sidebar.multiselect("Account code", _opts("account_code"))
f_sha = st.sidebar.multiselect("SHA code", _opts("sha_code"))
f_srhr = st.sidebar.multiselect("SRHR code", _opts("srhr_code"))
f_review = st.sidebar.multiselect("Review status", _opts("review_status"))
f_role = st.sidebar.multiselect("Record role", _opts("record_role"), default=["standalone", "parent"])

flt = base.copy()
if f_ministry:
    flt = flt[flt["ministry_code"].isin(f_ministry)]
if f_account:
    flt = flt[flt["account_code"].isin(f_account)]
if f_sha:
    flt = flt[flt["sha_code"].isin(f_sha)]
if f_srhr:
    flt = flt[flt["srhr_code"].isin(f_srhr)]
if f_review:
    flt = flt[flt["review_status"].isin(f_review)]
if f_role:
    flt = flt[flt["record_role"].isin(f_role)]

# ---------------------------------------------------------------- tabs
tab_overview, tab_tx, tab_review, tab_dq, tab_detail = st.tabs(
    ["Overview", "Transactions", "Review queue", "Data quality", "Record detail"])

with tab_overview:
    src_rows = q("SELECT SUM(rows_read) n FROM source_file")["n"][0]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Countries", len(countries))
    c2.metric("Source records", int(src_rows))
    c3.metric("Harmonised records", len(base))
    c4.metric("SHA mapped", int(base["sha_code"].notna().sum()))
    c5.metric("SRHR mapped", int(base["srhr_code"].notna().sum()))
    c6, c7, c8 = st.columns(3)
    c6.metric("Review queue", int((base["review_status"] == "needs_review").sum()))
    c7.metric("DQ issues", int(q("SELECT COUNT(*) n FROM dq_issues")["n"][0]))
    children = int((base["record_role"] == "child").sum())
    c8.metric("Child records (excl. from totals)", children)

    st.subheader("Expenditure by country and SHA code (countable records)")
    agg = (flt[flt["include_in_totals"]]
           .groupby(["country_code", "sha_code"], dropna=False)["amount"].sum()
           .reset_index().sort_values(["country_code", "amount"], ascending=[True, False]))
    st.dataframe(agg, use_container_width=True, hide_index=True)

    st.subheader("Ingestion batches")
    st.dataframe(q("SELECT * FROM ingestion_batch ORDER BY run_at DESC"),
                 use_container_width=True, hide_index=True)
    st.subheader("Source files & reconciliation checks")
    st.dataframe(q("SELECT country_code, file_name, format, rows_read, rows_loaded, file_checks "
                   "FROM source_file"), use_container_width=True, hide_index=True)

with tab_tx:
    st.subheader(f"{len(flt)} harmonised transactions")
    cols = ["country_code", "source_transaction_id", "transaction_date", "fiscal_year",
            "ministry_code", "account_code", "account_label", "description",
            "amount", "currency", "record_role", "sha_code", "srhr_code", "review_status"]
    st.dataframe(flt[cols], use_container_width=True, hide_index=True)

with tab_review:
    queue = flt[flt["review_status"] == "needs_review"]
    st.subheader(f"{len(queue)} records awaiting review")
    st.dataframe(
        queue[["country_code", "source_transaction_id", "account_code", "account_label",
               "description", "amount", "currency", "sha_code", "srhr_code",
               "sha_method", "sha_confidence", "srhr_confidence", "rationale"]],
        use_container_width=True, hide_index=True)

    st.divider()
    st.caption("Select a record to mark as reviewed.")
    options = queue["record_id"].tolist()
    sel = st.selectbox("Record", options, index=None,
                       format_func=lambda rid: f"{rid} — " +
                       str(queue.loc[queue['record_id'] == rid, 'source_transaction_id'].iloc[0]))
    if sel and st.button("Mark reviewed"):
        get_con().execute(
            "UPDATE classifications SET review_status='reviewed' WHERE record_id=?", [sel])
        st.cache_data.clear()
        st.success(f"{sel} marked as reviewed.")

with tab_dq:
    st.subheader("Issues by country and type")
    dq = q("SELECT * FROM dq_issues WHERE country_code IN (SELECT UNNEST($1))", [f_country])
    summ = (dq.groupby(["country_code", "issue_type", "severity"]).size()
            .reset_index(name="count").sort_values(["country_code", "count"], ascending=[True, False]))
    st.dataframe(summ, use_container_width=True, hide_index=True)
    st.subheader("Issue records")
    st.dataframe(dq[["country_code", "issue_type", "severity", "field_name",
                     "observed_value", "detail", "status"]],
                 use_container_width=True, hide_index=True)

with tab_detail:
    st.subheader("Record detail & source lineage")
    ids = flt["record_id"].tolist()
    pick = st.selectbox("Harmonised record", ids, index=None,
                        format_func=lambda rid: " / ".join(
                            str(x) for x in flt.loc[flt["record_id"] == rid,
                            ["country_code", "source_transaction_id"]].iloc[0]))
    if pick:
        rec = q("SELECT * FROM transactions WHERE record_id=?", [pick]).iloc[0]
        cls = q("SELECT * FROM classifications WHERE record_id=?", [pick])
        iss = q("SELECT issue_type, severity, field_name, observed_value, detail "
                "FROM dq_issues WHERE record_id=?", [pick])
        kids = q("SELECT source_transaction_id, description, amount, currency "
                 "FROM transactions WHERE parent_transaction_id=? AND country_code=?",
                 [rec["source_transaction_id"], rec["country_code"]])

        left, right = st.columns(2)
        with left:
            st.markdown("**Canonical record**")
            st.json({k: (str(v) if pd.notna(v) else None) for k, v in rec.items()})
        with right:
            st.markdown("**Classification**")
            st.json(cls.to_dict(orient="records"))
            st.markdown("**Data-quality issues on this record**")
            st.dataframe(iss, use_container_width=True, hide_index=True)
            if not kids.empty:
                st.markdown("**Sub-transactions (splits — excluded from totals)**")
                st.dataframe(kids, use_container_width=True, hide_index=True)

        st.markdown("**Source lineage**")
        st.code(
            f"file: {rec['source_file']}\n"
            f"location: {rec['source_row_or_path']}\n"
            f"source transaction id: {rec['source_transaction_id']}\n"
            f"batch: {rec['batch_id']}\n"
            f"record hash: {rec['record_hash']}", language="text")
        st.markdown("**Raw source payload (verbatim)**")
        st.json(rec["raw_payload"])
