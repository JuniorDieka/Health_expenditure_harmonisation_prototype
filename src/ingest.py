"""Country ingestion adapters.

Each adapter reads one raw source file and produces ``RawRecord`` objects
carrying both harmonised field values and complete source lineage
(file name, row / JSON path, source transaction id, raw payload).

Adapters never modify the source files and never discard records; parse
problems are attached to the record as data-quality issues instead.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openpyxl

from .parsing import fiscal_year_label, parse_amount, parse_date


@dataclass
class RawRecord:
    """One source row, normalised toward the canonical model."""

    country_code: str
    source_file: str
    source_row_or_path: str
    source_transaction_id: Optional[str]
    transaction_date: Any = None
    fiscal_year: Optional[str] = None
    ministry_code: Optional[str] = None
    ministry_name: Optional[str] = None
    account_code: Optional[str] = None
    account_label: Optional[str] = None
    description: Optional[str] = None
    supplier: Optional[str] = None
    amount: Optional[Decimal] = None
    raw_amount_text: Optional[str] = None
    currency: Optional[str] = None
    parent_transaction_id: Optional[str] = None
    record_role: str = "standalone"  # standalone | parent | child
    raw_payload: str = "{}"


@dataclass
class IngestIssue:
    """A data-quality observation emitted during ingestion."""

    issue_type: str
    severity: str  # info | warning | error
    field_name: Optional[str]
    observed_value: Optional[str]
    detail: str
    source_row_or_path: Optional[str] = None  # resolved to record later


@dataclass
class IngestResult:
    country_code: str
    file_name: str
    rows_read: int = 0
    records: List[RawRecord] = field(default_factory=list)
    issues: List[IngestIssue] = field(default_factory=list)
    coa_entries: List[Dict[str, str]] = field(default_factory=list)
    file_checks: List[Dict[str, Any]] = field(default_factory=list)


def _json_safe(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _modal_label(counts: Dict[str, int]) -> str:
    """Most frequent spelling of a label (used to derive a CoA label)."""
    return max(counts.items(), key=lambda kv: kv[1])[0]


# --------------------------------------------------------------------------
# Country A: CSV
# --------------------------------------------------------------------------

def ingest_country_a(path: Path, cfg: dict) -> IngestResult:
    res = IngestResult(country_code="CTA", file_name=path.name)
    col = cfg["columns"]
    label_votes: Dict[str, Dict[str, int]] = {}

    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for idx, row in enumerate(reader):
            res.rows_read += 1
            line_no = idx + 2  # header is line 1
            loc = f"{path.name}:row {line_no}"
            rec = RawRecord(
                country_code="CTA",
                source_file=path.name,
                source_row_or_path=loc,
                source_transaction_id=(row.get(col["txn_id"]) or "").strip() or None,
                ministry_code=(row.get(col["ministry_code"]) or "").strip() or None,
                ministry_name=(row.get(col["ministry_name"]) or "").strip() or None,
                account_code=(row.get(col["account_code"]) or "").strip() or None,
                description=(row.get(col["description"]) or "").strip() or None,
                supplier=(row.get(col["supplier"]) or "").strip() or None,
                currency=cfg["primary_currency"],
                raw_payload=_json_safe(row),
            )
            rec.transaction_date = parse_date(row.get(col["date"]), cfg["date_format"])
            if rec.transaction_date is None:
                res.issues.append(IngestIssue(
                    "unparseable_date", "error", col["date"],
                    str(row.get(col["date"])), "date could not be parsed", loc))
            else:
                rec.fiscal_year = fiscal_year_label(rec.transaction_date, cfg["fiscal_year_start_month"])

            raw_amt = row.get(col["amount"])
            rec.raw_amount_text = None if raw_amt is None else str(raw_amt)
            rec.amount, note = parse_amount(raw_amt)
            if note == "missing":
                res.issues.append(IngestIssue(
                    "missing_amount", "warning", col["amount"], None,
                    "amount cell empty", loc))
            elif note and note.startswith("unparseable"):
                res.issues.append(IngestIssue(
                    "unparseable_amount", "error", col["amount"], str(raw_amt),
                    "amount could not be parsed", loc))
            elif note:
                res.issues.append(IngestIssue(
                    "amount_format_normalised", "info", col["amount"], str(raw_amt),
                    f"amount stored as text; normalised ({note})", loc))

            if rec.amount is not None and rec.amount < 0:
                res.issues.append(IngestIssue(
                    "negative_amount", "warning", col["amount"], str(rec.amount),
                    "negative transaction preserved (reversal/correction)", loc))

            if rec.account_code and rec.description:
                votes = label_votes.setdefault(rec.account_code, {})
                votes[rec.description] = votes.get(rec.description, 0) + 1
            res.records.append(rec)

    # No official CoA file for Country A: derive the canonical label per code
    # as the most frequent description casing observed.
    for code, votes in sorted(label_votes.items()):
        label = _modal_label(votes)
        res.coa_entries.append({"country_code": "CTA", "account_code": code,
                                "account_label": label, "source": "derived:modal description"})
    label_map = {e["account_code"]: e["account_label"] for e in res.coa_entries}
    for rec in res.records:
        rec.account_label = label_map.get(rec.account_code)
    return res


# --------------------------------------------------------------------------
# Country B: Excel (report layout with metadata header + TOTAL footer)
# --------------------------------------------------------------------------

def ingest_country_b(path: Path, cfg: dict) -> IngestResult:
    res = IngestResult(country_code="CTB", file_name=path.name)
    col = cfg["columns"]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    # --- local chart of accounts sheet ---
    coa_map: Dict[str, str] = {}
    ws_coa = wb[cfg["coa_sheet"]]
    rows = list(ws_coa.iter_rows(values_only=True))
    for r in rows[1:]:  # skip its header row
        if r and r[0]:
            code = str(r[0]).strip()
            label = str(r[1]).strip() if r[1] else ""
            coa_map[code] = label
            res.coa_entries.append({"country_code": "CTB", "account_code": code,
                                    "account_label": label, "source": cfg["coa_sheet"]})

    # --- expenditure sheet ---
    ws = wb[cfg["sheet"]]
    header_row = cfg["header_row"]
    header: Optional[List[str]] = None
    parsed_sum = Decimal("0")
    parsed_count = 0
    total_claimed: Optional[Decimal] = None

    for excel_row, values in enumerate(ws.iter_rows(values_only=True), start=1):
        if excel_row < header_row:
            continue
        if excel_row == header_row:
            header = [str(v).strip() if v is not None else "" for v in values]
            continue
        if header is None:
            continue
        row = dict(zip(header, values))
        loc = f"{path.name}:{cfg['sheet']}!row {excel_row}"
        txn_id = row.get(col["txn_id"])
        if txn_id is None or str(txn_id).strip() == "":
            continue
        txn_id = str(txn_id).strip()
        res.rows_read += 1

        if txn_id == cfg["total_marker"]:
            total_claimed, _ = parse_amount(row.get(col["amount"]))
            res.rows_read -= 1  # footer is not a transaction
            continue

        rec = RawRecord(
            country_code="CTB",
            source_file=path.name,
            source_row_or_path=loc,
            source_transaction_id=txn_id,
            ministry_code=str(row.get(col["ministry_code"]) or "").strip() or None,
            ministry_name=str(row.get(col["ministry_name"]) or "").strip() or None,
            account_code=str(row.get(col["account_code"]) or "").strip() or None,
            description=str(row.get(col["description"]) or "").strip() or None,
            supplier=str(row.get(col["supplier"]) or "").strip() or None,
            currency=cfg["primary_currency"],
            raw_payload=_json_safe(row),
        )
        rec.account_label = coa_map.get(rec.account_code)
        rec.transaction_date = parse_date(row.get(col["date"]), cfg["date_format"])
        if rec.transaction_date is None:
            res.issues.append(IngestIssue(
                "unparseable_date", "error", col["date"],
                str(row.get(col["date"])), "date could not be parsed", loc))
        else:
            rec.fiscal_year = fiscal_year_label(rec.transaction_date, cfg["fiscal_year_start_month"])

        raw_amt = row.get(col["amount"])
        rec.raw_amount_text = None if raw_amt is None else str(raw_amt)
        rec.amount, note = parse_amount(raw_amt)
        if note == "missing":
            res.issues.append(IngestIssue(
                "missing_amount", "warning", col["amount"], None, "amount cell empty", loc))
        elif note and note.startswith("unparseable"):
            res.issues.append(IngestIssue(
                "unparseable_amount", "error", col["amount"], str(raw_amt),
                "amount could not be parsed", loc))
        elif note:
            res.issues.append(IngestIssue(
                "amount_format_normalised", "info", col["amount"], str(raw_amt),
                f"amount stored as text; normalised ({note})", loc))

        if rec.amount is not None and rec.amount < 0:
            res.issues.append(IngestIssue(
                "negative_amount", "warning", col["amount"], str(rec.amount),
                "negative transaction preserved (reversal/correction)", loc))

        if rec.amount is not None:
            parsed_sum += rec.amount
            parsed_count += 1
        res.records.append(rec)

    wb.close()

    # Reconcile the report's TOTAL footer against the parsed sum.
    if total_claimed is not None:
        ok = parsed_sum == total_claimed
        res.file_checks.append({
            "check": "total_row_reconciliation",
            "claimed": str(total_claimed),
            "computed": str(parsed_sum),
            "status": "pass" if ok else "fail",
        })
        res.issues.append(IngestIssue(
            "source_total_check", "info" if ok else "error", col["amount"],
            str(total_claimed),
            f"TOTAL footer {'reconciles to' if ok else 'DOES NOT reconcile to'} "
            f"parsed sum {parsed_sum}", None))
    return res


# --------------------------------------------------------------------------
# Country C: JSON (nested sub-transactions, multi-currency)
# --------------------------------------------------------------------------

def ingest_country_c(path: Path, cfg: dict) -> IngestResult:
    res = IngestResult(country_code="CTC", file_name=path.name)
    col = cfg["columns"]
    payload = json.loads(path.read_text(encoding="utf-8"))
    txns = payload[cfg["records_path"]]
    meta = payload.get("metadata", {})

    declared = meta.get("recordCount")
    if declared is not None and declared != len(txns):
        res.issues.append(IngestIssue(
            "metadata_count_mismatch", "warning", "recordCount", str(declared),
            f"metadata declares {declared} records; file contains {len(txns)}", None))
    res.file_checks.append({"check": "metadata_record_count",
                            "claimed": str(declared), "computed": str(len(txns)),
                            "status": "pass" if declared == len(txns) else "fail"})

    label_votes: Dict[str, Dict[str, int]] = {}

    def base_record(t: dict, loc: str) -> RawRecord:
        rec = RawRecord(
            country_code="CTC",
            source_file=path.name,
            source_row_or_path=loc,
            source_transaction_id=str(t.get(col["txn_id"]) or "").strip() or None,
            ministry_code=t.get(col["ministry_code"]),
            ministry_name=t.get(col["ministry_name"]),
            account_code=str(t.get(col["account_code"]) or "").strip() or None,
            description=t.get(col["description"]),
            supplier=t.get(col["supplier"]),
            currency=t.get(col["currency"]) or cfg["primary_currency"],
            raw_payload=_json_safe(t),
        )
        rec.transaction_date = parse_date(t.get(col["date"]), cfg["date_format"])
        rec.fiscal_year = t.get(col["fiscal_year"]) or (
            fiscal_year_label(rec.transaction_date, cfg["fiscal_year_start_month"])
            if rec.transaction_date else None)
        rec.raw_amount_text = None if t.get(col["amount"]) is None else str(t.get(col["amount"]))
        rec.amount, note = parse_amount(t.get(col["amount"]))
        if note == "missing":
            res.issues.append(IngestIssue(
                "missing_amount", "warning", col["amount"], None, "amount missing", loc))
        return rec

    def check_common(rec: RawRecord, t: dict, loc: str) -> None:
        if rec.transaction_date is None:
            res.issues.append(IngestIssue(
                "unparseable_date", "error", col["date"], str(t.get(col["date"])),
                "date could not be parsed", loc))
        elif rec.fiscal_year:
            computed = fiscal_year_label(rec.transaction_date, cfg["fiscal_year_start_month"])
            if computed != rec.fiscal_year:
                res.issues.append(IngestIssue(
                    "fiscal_year_mismatch", "warning", col["date"], str(t.get(col["date"])),
                    f"posting date implies {computed} but record states {rec.fiscal_year}", loc))
        if rec.currency != cfg["primary_currency"]:
            res.issues.append(IngestIssue(
                "non_primary_currency", "info", col["currency"], rec.currency,
                f"amount denominated in {rec.currency}; no FX conversion applied", loc))
        if t.get(col["description"]) in (None, ""):
            res.issues.append(IngestIssue(
                "missing_description", "warning", col["description"], None,
                "description missing; classification falls back to account code", loc))
        if t.get(col["supplier"]) in (None, ""):
            res.issues.append(IngestIssue(
                "missing_supplier", "info", col["supplier"], None,
                "supplier field empty", loc))

    for i, t in enumerate(txns):
        res.rows_read += 1
        loc = f"{path.name}:transactions[{i}]"
        rec = base_record(t, loc)
        children = t.get(col["children"]) or []
        if children:
            rec.record_role = "parent"
        check_common(rec, t, loc)
        res.records.append(rec)
        if rec.account_code and rec.description:
            votes = label_votes.setdefault(rec.account_code, {})
            votes[rec.description] = votes.get(rec.description, 0) + 1

        if children:
            child_sum = Decimal("0")
            for j, s in enumerate(children):
                sub_loc = f"{path.name}:transactions[{i}].subTransactions[{j}]"
                sub = RawRecord(
                    country_code="CTC",
                    source_file=path.name,
                    source_row_or_path=sub_loc,
                    source_transaction_id=str(s.get("subId") or "").strip() or None,
                    transaction_date=rec.transaction_date,
                    fiscal_year=rec.fiscal_year,
                    ministry_code=rec.ministry_code,
                    ministry_name=rec.ministry_name,
                    account_code=rec.account_code,
                    description=s.get("description"),
                    supplier=rec.supplier,
                    currency=rec.currency,
                    parent_transaction_id=rec.source_transaction_id,
                    record_role="child",
                    raw_payload=_json_safe(s),
                )
                sub.raw_amount_text = None if s.get("amount") is None else str(s.get("amount"))
                sub.amount, _ = parse_amount(s.get("amount"))
                if sub.amount is not None:
                    child_sum += sub.amount
                res.records.append(sub)
            if rec.amount is not None and child_sum != rec.amount:
                res.issues.append(IngestIssue(
                    "child_sum_mismatch", "warning", col["children"],
                    f"parent={rec.amount} children={child_sum}",
                    f"sub-transaction sum differs from parent by {rec.amount - child_sum}",
                    loc))

    for code, votes in sorted(label_votes.items()):
        res.coa_entries.append({"country_code": "CTC", "account_code": code,
                                "account_label": _modal_label(votes),
                                "source": "derived:modal description"})
    label_map = {e["account_code"]: e["account_label"] for e in res.coa_entries}
    for rec in res.records:
        rec.account_label = label_map.get(rec.account_code)
    return res


# --------------------------------------------------------------------------
# Dispatch
# --------------------------------------------------------------------------

_ADAPTERS = {
    ("CTA", "csv"): ingest_country_a,
    ("CTB", "xlsx"): ingest_country_b,
    ("CTC", "json"): ingest_country_c,
}


def ingest_country(country_code: str, cfg: dict, raw_dir: Path) -> IngestResult:
    """Run the adapter matching the country's declared format."""
    path = raw_dir / cfg["file"]
    adapter = _ADAPTERS[(country_code, cfg["format"])]
    return adapter(path, cfg)
