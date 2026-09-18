"""Dataset-level data-quality validation.

Runs after harmonisation over the canonical table. Parse-level issues are
already emitted by the adapters; these checks need the whole dataset in
view (duplicates, adversarial free text, description/CoA-label mismatch,
orphaned child records).
"""

from __future__ import annotations

import re
from typing import Dict, List

import pandas as pd

from .parsing import DEFAULT_ADVERSARIAL_MARKERS, detect_adversarial

_SPLIT_SUFFIX_RE = re.compile(r"\s*-\s*split\s+\d+/\d+\s*$", re.IGNORECASE)


def _norm(text: str | None) -> str:
    """Normalise a label/description for comparison purposes."""
    if not text:
        return ""
    s = _SPLIT_SUFFIX_RE.sub("", str(text))
    return re.sub(r"\s+", " ", s).strip().lower()


def check_dataset(df: pd.DataFrame, markers=DEFAULT_ADVERSARIAL_MARKERS) -> List[Dict]:
    """Return issue dicts for records in the harmonised dataframe."""
    issues: List[Dict] = []

    def add(rec, issue_type, severity, field_name, observed, detail):
        issues.append({
            "record_id": rec["record_id"],
            "country_code": rec["country_code"],
            "issue_type": issue_type,
            "severity": severity,
            "field_name": field_name,
            "observed_value": None if observed is None else str(observed)[:500],
            "detail": detail,
            "status": "open",
        })

    # --- duplicate source transaction ids within a country ----------------
    ids = df[df["source_transaction_id"].notna()]
    dup_mask = ids.duplicated(subset=["country_code", "source_transaction_id"], keep=False)
    for _, rec in ids[dup_mask].iterrows():
        add(rec, "duplicate_transaction_id", "warning", "source_transaction_id",
            rec["source_transaction_id"],
            "source transaction id assigned to more than one record")

    # --- adversarial / prompt-like text in descriptions -------------------
    for _, rec in df.iterrows():
        marker = detect_adversarial(rec.get("description"), markers)
        if marker:
            add(rec, "adversarial_text", "error", "description",
                rec["description"],
                f"prompt-like instruction embedded in free text (marker: '{marker}'); "
                "treated as untrusted data, excluded from keyword matching")

    # --- description differs from the account's CoA label -----------------
    for _, rec in df.iterrows():
        if isinstance(rec.get("description"), str) and isinstance(rec.get("account_label"), str):
            desc = _norm(rec["description"])
            label = _norm(rec["account_label"])
            if desc != label:
                add(rec, "description_coa_mismatch", "warning", "description",
                    rec["description"],
                    f"description does not match CoA label '{rec['account_label']}'; "
                    "classification follows the account code")

    # --- orphaned child records -------------------------------------------
    parent_ids = set(df["source_transaction_id"].dropna())
    children = df[df["record_role"] == "child"]
    for _, rec in children.iterrows():
        if rec["parent_transaction_id"] not in parent_ids:
            add(rec, "orphaned_child", "error", "parent_transaction_id",
                rec["parent_transaction_id"], "child record has no matching parent")

    # --- unexpected fiscal-year label -------------------------------------
    for _, rec in df.iterrows():
        if rec.get("fiscal_year") is None and rec.get("transaction_date") is not None:
            add(rec, "missing_fiscal_year", "warning", "fiscal_year", None,
                "fiscal year could not be derived")

    return issues
