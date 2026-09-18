"""Value parsing and text-sanitisation helpers.

Pure functions kept separate from I/O so they are trivially testable and
reusable across country adapters.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

# Patterns that indicate prompt-like / adversarial content embedded in
# free text. Matched case-insensitively against the *lower-cased* text.
DEFAULT_ADVERSARIAL_MARKERS = (
    "ignore all previous",
    "ignorez tout",
    "previous instructions",
    "[/inst]",
    "[inst]",
    "<<system",
    "<</system",
    "note for reviewer",
    "system override",
    "do not reclassify",
    "do not explain",
    "you must now",
    "respond that",
    "repondre uniquement",
)

_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")
_THOUSANDS_RE = re.compile(r"^-?\d{1,3}([, ]\d{3})+([.,]\d+)?$")
_DECIMAL_COMMA_RE = re.compile(r"^-?\d+,\d{1,2}$")


def parse_amount(value) -> Tuple[Optional[Decimal], Optional[str]]:
    """Parse a raw amount cell into a Decimal.

    Returns (amount, note). ``note`` is None for clean numerics, otherwise a
    short string describing the normalisation applied (or the failure).
    Handles: int/float/Decimal, "29,998.63" quoted thousands, French
    decimal-comma "11548910,00", and suffixed "31,073,710 FCFA".
    """
    if value is None:
        return None, "missing"
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value)), None

    s = str(value).strip().strip('"').strip()
    if not s:
        return None, "missing"

    had_currency = False
    if s.upper().endswith("FCFA"):
        s = s[:-4].strip()
        had_currency = True
    s = s.replace("\u00a0", " ").strip()

    if _NUMERIC_RE.match(s):
        return Decimal(s), ("currency_suffix" if had_currency else None)

    if _DECIMAL_COMMA_RE.match(s) and not had_currency:
        # French decimal comma: 11548910,00
        return Decimal(s.replace(",", ".")), "decimal_comma"

    if _THOUSANDS_RE.match(s) or had_currency:
        cleaned = s.replace(",", "").replace(" ", "")
        try:
            return Decimal(cleaned), "thousands_separator" + ("+currency" if had_currency else "")
        except InvalidOperation:
            pass

    # Last resort: strip everything except digits, sign and separators.
    cleaned = re.sub(r"[^\d.,-]", "", s).replace(",", "")
    try:
        return Decimal(cleaned), "aggressive_clean"
    except InvalidOperation:
        return None, f"unparseable:{s[:40]}"


def parse_date(value, fmt: str) -> Optional[date]:
    """Parse a date string using the country's declared format."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, fmt).date()
    except ValueError:
        # Tolerate ISO strings even if a different format was declared.
        try:
            return datetime.fromisoformat(s).date()
        except ValueError:
            return None


def fiscal_year_label(d: date, fy_start_month: int) -> str:
    """Return a fiscal-year label like 'FY2023/24' for a date.

    ``fy_start_month`` is the calendar month in which the fiscal year begins
    (7 = July, 10 = October, 1 = January).
    """
    if fy_start_month == 1:
        return f"FY{d.year}"
    start_year = d.year if d.month >= fy_start_month else d.year - 1
    return f"FY{start_year}/{str(start_year + 1)[-2:]}"


def detect_adversarial(text: Optional[str], markers=DEFAULT_ADVERSARIAL_MARKERS) -> Optional[str]:
    """Return the first adversarial marker found in *text*, else None."""
    if not isinstance(text, str) or not text:
        return None
    low = text.lower()
    for m in markers:
        if m in low:
            return m
    return None


def sanitize_text(text: Optional[str], markers=DEFAULT_ADVERSARIAL_MARKERS) -> Optional[str]:
    """Produce a safe text for downstream keyword/rule matching.

    Truncates the text at the first adversarial marker, strips control
    characters, collapses whitespace and caps length. The *raw* value is
    always preserved separately in ``raw_payload``; this is only ever used
    for matching, never for storage of record.
    """
    if text is None:
        return None
    s = str(text)
    low = s.lower()
    cut = len(s)
    for m in markers:
        idx = low.find(m)
        if idx != -1:
            # keep any legitimate text that precedes the marker
            cut = min(cut, idx)
    s = s[:cut]
    s = re.sub(r"[\x00-\x1f\x7f]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:500] if s else None
