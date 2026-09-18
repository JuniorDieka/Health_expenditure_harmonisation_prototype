# Health Expenditure Harmonisation — Prototype

Ingests heterogeneous country expenditure extracts (CSV / Excel / JSON),
harmonises them into one auditable canonical model in DuckDB, classifies
each record against SHA and SRHR reference codes, surfaces data-quality
issues, and gives analysts a Streamlit review interface with full source
lineage.

## Architecture

- `src/ingest.py` — one adapter per country; emits `RawRecord`s with
  source lineage (file, row/JSON path, source id) + parse-level DQ issues.
- `src/harmonize.py` — canonical schema; record ids and content hashes;
  child sub-transactions flagged `include_in_totals=false` (no double count).
- `src/quality.py` — dataset-level checks: duplicate ids, adversarial
  free text, description/CoA-label mismatch, orphaned children.
- `src/classify.py` — deterministic cascade: exact CoA map → sanitised
  keyword rules → review queue. SHA and SRHR resolved independently.
- `src/database.py` — DuckDB schema (transactions, classifications,
  dq_issues, ref_*, batches/files).
- `app.py` — Streamlit UI: KPIs, filters, review queue, record lineage.
- `config/*.yaml` — adapter layout, CoA→SHA/SRHR maps, keyword rules.
  All mapping logic lives outside the code so country teams can edit it.

## Setup & run

```bash
pip install -r requirements.txt
python -m src.pipeline        # rebuild output/harmonised.duckdb from Data/raw
streamlit run app.py          # analyst UI
python -m pytest tests/       # tests
```

## Key assumptions

- Source files are immutable; the DuckDB database is rebuilt each run.
- No FX rates were supplied, so amounts stay in their original currency
  (208 USD records flagged `non_primary_currency`, never converted).
- Simplified SHA reference has no capital-formation code; capital
  expenditure (construction, ambulances) is routed to review, not forced.
- Source descriptions are untrusted data: adversarial text is flagged,
  sanitised for matching, and can never override CoA rules.

## Limitations / next steps

- No authentication; analyst "mark reviewed" writes back to DuckDB only.
- Fiscal-year alignment across countries is labelled, not yet normalised
  into a single reporting period for cross-country totals.
- Production: add FX tables, versioned mapping governance, a proper
  review-workflow store, and optional AI-assisted suggestions behind the
  same rule interface.
