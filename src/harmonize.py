"""Harmonisation: RawRecord to the canonical expenditure model.

Assigns stable surrogate ids, sanitises free text for downstream matching,
computes record hashes, and decides whether a record counts toward
expenditure totals (parents/standalone yes; children no; they are splits
of their parent, so counting both would double count).
"""

from __future__ import annotations

import hashlib
from typing import List

import pandas as pd

from .ingest import RawRecord
from .parsing import sanitize_text

CANONICAL_COLUMNS = [
    "record_id", "batch_id", "country_code", "source_file",
    "source_row_or_path", "source_transaction_id", "transaction_date",
    "fiscal_year", "ministry_code", "ministry_name", "account_code",
    "account_label", "description", "description_sanitized", "supplier",
    "amount", "raw_amount_text", "currency", "parent_transaction_id",
    "record_role", "include_in_totals", "raw_payload", "record_hash",
]


def _record_id(country_code: str, source_file: str, loc: str, txn_id: str | None) -> str:
    """Deterministic surrogate key from source lineage.

    Lineage (not the source transaction id) is used because source ids can
    collide, e.g. Country A's duplicated KE-2401203.
    """
    digest = hashlib.sha1(
        f"{country_code}|{source_file}|{loc}|{txn_id or ''}".encode("utf-8")
    ).hexdigest()
    return f"R{digest[:15]}"


def _record_hash(rec: RawRecord) -> str:
    """Content hash over canonical values for change/evidence detection."""
    parts = [
        rec.country_code, str(rec.transaction_date), str(rec.ministry_code),
        str(rec.account_code), str(rec.amount), str(rec.currency),
        str(rec.source_transaction_id), str(rec.description),
        str(rec.supplier), str(rec.parent_transaction_id),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def harmonize(records: List[RawRecord], batch_id: str) -> pd.DataFrame:
    """Build the canonical transactions table for a batch of records."""
    rows = []
    for rec in records:
        rows.append({
            "record_id": _record_id(rec.country_code, rec.source_file,
                                    rec.source_row_or_path, rec.source_transaction_id),
            "batch_id": batch_id,
            "country_code": rec.country_code,
            "source_file": rec.source_file,
            "source_row_or_path": rec.source_row_or_path,
            "source_transaction_id": rec.source_transaction_id,
            "transaction_date": rec.transaction_date,
            "fiscal_year": rec.fiscal_year,
            "ministry_code": rec.ministry_code,
            "ministry_name": rec.ministry_name,
            "account_code": rec.account_code,
            "account_label": rec.account_label,
            "description": rec.description,
            "description_sanitized": sanitize_text(rec.description),
            "supplier": rec.supplier,
            "amount": float(rec.amount) if rec.amount is not None else None,
            "raw_amount_text": rec.raw_amount_text,
            "currency": rec.currency,
            "parent_transaction_id": rec.parent_transaction_id,
            "record_role": rec.record_role,
            # children are informational splits of their parent amount
            "include_in_totals": rec.record_role != "child",
            "raw_payload": rec.raw_payload,
            "record_hash": _record_hash(rec),
        })
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
