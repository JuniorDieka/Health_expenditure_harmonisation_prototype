"""Analyst review interface. Run with: streamlit run app.py

Read mostly UI over the harmonised DuckDB database: overview KPIs,
data quality summary, filterable transaction table, classification
review queue, and per record source lineage.

Visual identity follows WHO branding: primary blue #008DC9 (Pantone 299C),
navy #0F2D5B, light blue #90D3FB, accent orange #F39313.
"""

from __future__ import annotations

import json
from pathlib import Path

import altair as alt
import duckdb
import pandas as pd
import streamlit as st
import yaml

# ------------------------------------------------------------------ branding

WHO_BLUE = "#008DC9"
WHO_NAVY = "#0F2D5B"
WHO_LIGHT = "#90D3FB"
WHO_PALE = "#EFF6FB"
WHO_GREY = "#DADADA"
WHO_ORANGE = "#F39313"
WHO_TEXT = "#1D1D1B"
SEV_COLORS = {"error": "#C0392B", "warning": "#E67E22", "info": "#008DC9"}

st.set_page_config(
    page_title="Health Expenditure Harmonisation",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(f"""
<style>
    .who-banner {{
        background: linear-gradient(95deg, {WHO_NAVY} 0%, {WHO_BLUE} 78%, {WHO_LIGHT} 100%);
        border-radius: 14px;
        padding: 1.7rem 2.1rem 1.5rem 2.1rem;
        margin-bottom: 1.1rem;
        color: #FFFFFF;
    }}
    .who-banner h1 {{ color: #FFFFFF; margin: 0; font-size: 1.9rem; font-weight: 700; }}
    .who-banner p {{ color: #DCEEFB; margin: 0.35rem 0 0 0; font-size: 1.02rem; }}
    .who-tag {{
        display: inline-block; background: rgba(255,255,255,0.18);
        border: 1px solid rgba(255,255,255,0.35); color: #FFFFFF;
        border-radius: 999px; padding: 0.1rem 0.8rem; font-size: 0.78rem;
        margin-top: 0.7rem; letter-spacing: 0.04em;
    }}
    .kpi-card {{
        background: #FFFFFF; border: 1px solid {WHO_GREY}; border-top: 4px solid {WHO_BLUE};
        border-radius: 10px; padding: 0.85rem 1rem 0.6rem 1rem; min-height: 104px;
    }}
    .kpi-num {{ font-size: 1.65rem; font-weight: 700; color: {WHO_NAVY}; }}
    .kpi-label {{ font-size: 0.82rem; color: #5A6B7A; text-transform: uppercase; letter-spacing: 0.05em; }}
    .kpi-note {{ font-size: 0.78rem; color: #8CA0B0; margin-top: 0.15rem; }}
    .pill {{
        display: inline-block; border-radius: 999px; padding: 0.15rem 0.85rem;
        font-size: 0.82rem; font-weight: 600; margin-right: 0.4rem;
    }}
    .section-note {{ color: #5A6B7A; font-size: 0.88rem; margin-top: -0.6rem; }}
    .who-footer {{
        margin-top: 2.2rem; padding: 1rem 0 0.4rem 0;
        border-top: 3px solid {WHO_BLUE}; color: #5A6B7A; font-size: 0.82rem;
    }}
    div[data-testid="stMetric"] {{
        background: #FFFFFF; border: 1px solid {WHO_GREY};
        border-top: 4px solid {WHO_BLUE}; border-radius: 10px; padding: 0.7rem 1rem;
    }}
    div[data-testid="stMetric"] label {{ color: #5A6B7A !important; }}
    section[data-testid="stSidebar"] {{ background: {WHO_PALE}; }}
</style>
""", unsafe_allow_html=True)

CFG = yaml.safe_load(Path("config/countries.yaml").read_text(encoding="utf-8"))
DB_PATH = CFG["db_path"]


@st.cache_data(ttl=60)
def q(sql: str, params: list | None = None) -> pd.DataFrame:
    """Run a read only query. Short lived connections keep the DuckDB
    file lock free so several viewers or tools can attach at once."""
    with duckdb.connect(DB_PATH, read_only=True) as con:
        return con.execute(sql, params or []).df()


def mark_reviewed(record_id: str) -> None:
    """Persist an analyst decision with a short lived write connection."""
    with duckdb.connect(DB_PATH) as con:
        con.execute(
            "UPDATE classifications SET review_status='reviewed' WHERE record_id=?",
            [record_id])


def kpi(label: str, value, note: str = "") -> None:
    st.markdown(
        f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
        f'<div class="kpi-num">{value}</div><div class="kpi-note">{note}</div></div>',
        unsafe_allow_html=True)


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

# ------------------------------------------------------------------ header

st.markdown("""
<div class="who-banner">
    <h1>Health Expenditure Harmonisation</h1>
    <p>Country spending data, cleaned, unified and classified so it can be compared and reviewed.</p>
    <span class="who-tag">PROTOTYPE · SYNTHETIC ASSESSMENT DATA</span>
</div>
""", unsafe_allow_html=True)

if not Path(DB_PATH).exists():
    st.error("The database was not found. Run `python -m src.pipeline` first, then reload this page.")
    st.stop()

with st.expander("New to this tool? Read this first"):
    st.markdown("""
**What this tool does.** Three countries send their health spending data in different
formats (a spreadsheet export, a finance system report and a JSON extract). This tool
reads all of them, puts every transaction into one common structure, assigns each
record a health category, and flags anything that looks wrong or needs a human decision.

**Key terms used on this page**
- **SHA code**: System of Health Accounts category. It answers *what kind of health service or good was purchased* (for example immunisation, laboratory services, administration).
- **SRHR tag**: marks spending related to Sexual and Reproductive Health and Rights (for example family planning, maternal health, HIV).
- **Review queue**: records the system could not classify with confidence, or that carry data quality warnings. Nothing is hidden; these wait for an analyst.
- **Data quality issue**: a note attached to a record, such as a missing amount, a negative value, or suspicious text in the description.
- **Child records**: some Country C transactions are split into sub amounts. Only the parent counts in totals so nothing is counted twice.
- **Lineage**: every record keeps a link back to its exact source file and row, plus the original untouched text.
""")

# ------------------------------------------------------------------ sidebar

st.sidebar.markdown("## Filter the records")
st.sidebar.caption("Every table and chart below respects these filters.")

countries = sorted(q("SELECT DISTINCT country_code FROM transactions")["country_code"])
COUNTRY_NAMES = {"CTA": "Country A (KES)", "CTB": "Country B (XOF)", "CTC": "Country C (RWF)"}
f_country = st.sidebar.multiselect(
    "Country", countries, default=countries,
    format_func=lambda c: COUNTRY_NAMES.get(c, c))

base = q(f"{VIEW_SQL} WHERE t.country_code IN (SELECT UNNEST($1))", [f_country])


def _opts(col: str) -> list:
    return sorted(x for x in base[col].dropna().unique())


f_ministry = st.sidebar.multiselect("Ministry", _opts("ministry_code"))
f_account = st.sidebar.multiselect("Account code", _opts("account_code"))
f_sha = st.sidebar.multiselect("SHA health category", _opts("sha_code"))
f_srhr = st.sidebar.multiselect("SRHR tag", _opts("srhr_code"))
f_review = st.sidebar.multiselect(
    "Review status", _opts("review_status"),
    format_func=lambda s: {"auto_classified": "Auto classified",
                           "needs_review": "Needs review",
                           "reviewed": "Reviewed"}.get(s, s))
f_role = st.sidebar.multiselect(
    "Record role", _opts("record_role"), default=["standalone", "parent"],
    format_func=lambda s: {"standalone": "Standalone", "parent": "Parent",
                           "child": "Child (split, not counted)"}.get(s, s))

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

# ------------------------------------------------------------------ tabs

tab_overview, tab_tx, tab_review, tab_dq, tab_detail = st.tabs([
    "📊 Overview", "🧾 Transactions", "🔍 Review queue",
    "⚠️ Data quality", "🗂 Record detail"])

# ============================================================= OVERVIEW
with tab_overview:
    src_rows = int(q("SELECT SUM(rows_read) n FROM source_file")["n"][0])
    n_sha = int(base["sha_code"].notna().sum())
    n_srhr = int(base["srhr_code"].notna().sum())
    n_review = int((base["review_status"] == "needs_review").sum())
    n_dq = int(q("SELECT COUNT(*) n FROM dq_issues")["n"][0])
    n_children = int((base["record_role"] == "child").sum())

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi("Countries", len(countries), "reporting in this batch")
    with c2:
        kpi("Source records", f"{src_rows:,}", "rows read from raw files")
    with c3:
        kpi("Harmonised records", f"{len(base):,}", f"incl. {n_children} child splits")
    with c4:
        kpi("Classified", f"{n_sha:,} / {n_srhr:,}", "SHA / SRHR assigned")

    c5, c6, c7 = st.columns(3)
    with c5:
        kpi("Needs review", f"{n_review:,}", "waiting for an analyst")
    with c6:
        kpi("Data quality notes", f"{n_dq:,}", "issues attached to records")
    with c7:
        kpi("Reviewed", int((base['review_status'] == 'reviewed').sum()), "confirmed by analysts")

    st.write("")
    st.subheader("Where the money goes")
    st.markdown('<p class="section-note">Total spending per health category, per country. '
                'Only countable records are summed, so split amounts are never counted twice.</p>',
                unsafe_allow_html=True)
    agg = (flt[flt["include_in_totals"]]
           .groupby(["country_code", "sha_code"], dropna=False)["amount"].sum()
           .reset_index())
    agg["sha_code"] = agg["sha_code"].fillna("unclassified")
    chart = (alt.Chart(agg)
             .mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
             .encode(
                 x=alt.X("sha_code:N", title="SHA category", sort="-y"),
                 y=alt.Y("amount:Q", title="Total amount (local currency)"),
                 color=alt.Color("country_code:N", title="Country",
                                 scale=alt.Scale(range=[WHO_BLUE, WHO_NAVY, WHO_ORANGE])),
                 tooltip=["country_code", "sha_code", alt.Tooltip("amount:Q", format=",.0f")])
             .properties(height=320))
    st.altair_chart(chart, width='stretch')

    left, right = st.columns(2)
    with left:
        st.subheader("Review queue by country")
        rq = (flt[flt["review_status"] == "needs_review"]
              .groupby("country_code").size().reset_index(name="records"))
        ch = (alt.Chart(rq).mark_arc(innerRadius=55)
              .encode(theta="records:Q",
                      color=alt.Color("country_code:N", title="Country",
                                      scale=alt.Scale(range=[WHO_BLUE, WHO_NAVY, WHO_ORANGE])),
                      tooltip=["country_code", "records"]))
        st.altair_chart(ch, width='stretch')
    with right:
        st.subheader("Data quality issues by severity")
        dq_sev = (q("SELECT country_code, severity, COUNT(*) n FROM dq_issues "
                    "GROUP BY 1,2"))
        ch2 = (alt.Chart(dq_sev).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
               .encode(x=alt.X("country_code:N", title="Country"),
                       y=alt.Y("n:Q", title="Issues"),
                       color=alt.Color("severity:N", title="Severity",
                                       scale=alt.Scale(domain=list(SEV_COLORS),
                                                       range=list(SEV_COLORS.values()))),
                       tooltip=["country_code", "severity", "n"]))
        st.altair_chart(ch2, width='stretch')

    st.subheader("Source files and checks")
    st.markdown('<p class="section-note">Every input file, how many rows were read, '
                'and the built in reconciliation checks that ran during loading.</p>',
                unsafe_allow_html=True)
    sf = q("SELECT country_code, file_name, format, rows_read, rows_loaded, file_checks FROM source_file")
    sf["file_checks"] = sf["file_checks"].apply(
        lambda s: "; ".join(f"{c['check']}: {c['status']}" for c in json.loads(s)) if s else "")
    st.dataframe(sf, width='stretch', hide_index=True)

# ============================================================= TRANSACTIONS
with tab_tx:
    st.subheader(f"{len(flt):,} harmonised transactions")
    st.markdown('<p class="section-note">One row per spending record, unified across all three '
                'countries. Use the filters on the left to narrow this list.</p>',
                unsafe_allow_html=True)
    search = st.text_input("Search description, supplier or transaction id")
    view = flt
    if search:
        m = (view["description"].fillna("").str.contains(search, case=False)
             | view["supplier"].fillna("").str.contains(search, case=False)
             | view["source_transaction_id"].fillna("").str.contains(search, case=False))
        view = view[m]
    cols = ["country_code", "source_transaction_id", "transaction_date", "fiscal_year",
            "ministry_code", "account_code", "account_label", "description",
            "supplier", "amount", "currency", "record_role",
            "sha_code", "srhr_code", "review_status"]
    st.dataframe(
        view[cols], width='stretch', hide_index=True,
        column_config={
            "amount": st.column_config.NumberColumn(format="%,.2f"),
            "transaction_date": st.column_config.DateColumn(format="YYYY-MM-DD"),
        })

# ============================================================= REVIEW QUEUE
with tab_review:
    queue = flt[flt["review_status"] == "needs_review"]
    st.subheader(f"{len(queue):,} records need an analyst")
    st.markdown(
        '<p class="section-note">These records were not classified automatically, or carry a '
        'warning such as mismatched or suspicious description text. Each row shows the method '
        'that tried, its confidence, and the reason.</p>', unsafe_allow_html=True)

    def _row_color(row):
        if row["sha_code"] is None or pd.isna(row["sha_code"]):
            return ["background-color: #FDF1E7"] * len(row)
        return [""] * len(row)

    shown = queue[["country_code", "source_transaction_id", "account_code", "account_label",
                   "description", "amount", "currency", "sha_code", "srhr_code",
                   "sha_method", "sha_confidence", "srhr_confidence", "rationale"]]
    st.dataframe(
        shown.style.apply(_row_color, axis=1),
        width='stretch', hide_index=True,
        column_config={
            "sha_confidence": st.column_config.ProgressColumn(
                "SHA confidence", min_value=0, max_value=1, format="%.2f"),
            "srhr_confidence": st.column_config.ProgressColumn(
                "SRHR confidence", min_value=0, max_value=1, format="%.2f"),
            "amount": st.column_config.NumberColumn(format="%,.2f"),
        })
    st.caption("Rows highlighted in orange have no SHA category assigned yet.")

    st.divider()
    st.markdown("**Resolve a record**")
    st.caption("Pick a record you have checked against the source, then mark it as reviewed.")
    options = queue["record_id"].tolist()
    sel = st.selectbox(
        "Record", options, index=None,
        format_func=lambda rid: f"{queue.loc[queue['record_id'] == rid, 'country_code'].iloc[0]}  "
                                f"{queue.loc[queue['record_id'] == rid, 'source_transaction_id'].iloc[0]}")
    if sel and st.button("Mark as reviewed", type="primary"):
        mark_reviewed(sel)
        st.cache_data.clear()
        st.success("Record marked as reviewed.")
        st.rerun()

# ============================================================= DATA QUALITY
with tab_dq:
    st.subheader("Data quality summary")
    st.markdown('<p class="section-note">Problems found while reading and checking the source '
                'files. Records are never silently corrected or dropped; issues are attached '
                'to the record instead.</p>', unsafe_allow_html=True)

    dq = q("SELECT * FROM dq_issues WHERE country_code IN (SELECT UNNEST($1))", [f_country])
    sev_counts = dq.groupby("severity").size().to_dict()
    pills = " ".join(
        f'<span class="pill" style="background:{SEV_COLORS[s]}1A;color:{SEV_COLORS[s]};'
        f'border:1px solid {SEV_COLORS[s]}">{s}: {sev_counts.get(s, 0)}</span>'
        for s in ("error", "warning", "info"))
    st.markdown(pills, unsafe_allow_html=True)
    st.write("")

    st.markdown("**Issues by country and type**")
    summ = (dq.groupby(["country_code", "issue_type", "severity"]).size()
            .reset_index(name="count")
            .sort_values(["country_code", "count"], ascending=[True, False]))
    st.dataframe(summ, width='stretch', hide_index=True)

    with st.expander("Show every issue record"):
        st.dataframe(
            dq[["country_code", "issue_type", "severity", "field_name",
                "observed_value", "detail", "status"]],
            width='stretch', hide_index=True)

# ============================================================= RECORD DETAIL
with tab_detail:
    st.subheader("Record detail and source lineage")
    st.markdown('<p class="section-note">Everything known about one record: the cleaned values, '
                'how it was classified, any quality flags, and exactly where it came from.</p>',
                unsafe_allow_html=True)

    ids = flt["record_id"].tolist()
    pick = st.selectbox(
        "Choose a harmonised record", ids, index=None,
        format_func=lambda rid: "  ".join(
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
            st.markdown("#### What is this record?")
            st.caption("Cleaned, harmonised values.")
            st.json({k: (str(v) if pd.notna(v) else None) for k, v in rec.items()
                     if k not in ("raw_payload",)})
        with right:
            st.markdown("#### How was it classified?")
            st.caption("Codes, method used, confidence and the written rationale.")
            st.json(cls.to_dict(orient="records"))
            st.markdown("#### Quality flags on this record")
            if iss.empty:
                st.success("No issues flagged on this record.")
            else:
                st.dataframe(iss, width='stretch', hide_index=True)
            if not kids.empty:
                st.markdown("#### Splits of this transaction")
                st.caption("Child amounts are informational only and are excluded from totals.")
                st.dataframe(kids, width='stretch', hide_index=True)

        st.markdown("#### Where did it come from?")
        st.caption("Full lineage, so every figure can be traced back to its source document.")
        st.code(
            f"file:       {rec['source_file']}\n"
            f"location:   {rec['source_row_or_path']}\n"
            f"source id:  {rec['source_transaction_id']}\n"
            f"batch:      {rec['batch_id']}\n"
            f"hash:       {rec['record_hash']}", language="text")
        with st.expander("Original raw payload (verbatim, untouched)"):
            st.json(rec["raw_payload"])

# ------------------------------------------------------------------ footer

st.markdown(f"""
<div class="who-footer">
    <strong>Health Expenditure Harmonisation Prototype</strong> · built for a technical
    assessment · all data is synthetic
</div>
""", unsafe_allow_html=True)
