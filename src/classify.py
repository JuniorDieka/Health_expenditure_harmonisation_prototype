"""SHA / SRHR classification: deterministic, explainable cascade.

Order of precedence per axis (SHA and SRHR are resolved independently):
  1. coa_maps: exact chart-of-accounts mapping for the country (authoritative)
  2. keyword_rules: configurable substring rules on the *sanitised* description
  3. unresolved: axis left unclassified; record routed to the review queue

Free text is untrusted data: keyword rules only ever see the sanitised
description, and CoA/reference rules always outrank text. No code is ever
forced where evidence is insufficient.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml

from .parsing import detect_adversarial


def load_rules(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _keyword_match(axis: str, text: Optional[str], rules: List[dict]) -> Tuple[Optional[str], Optional[dict]]:
    """First keyword rule (in config order) matching the sanitised text."""
    if not isinstance(text, str) or not text:
        return None, None
    low = text.lower()
    for rule in rules:
        if rule["axis"] != axis:
            continue
        if any(p.lower() in low for p in rule["patterns"]):
            return rule["code"], rule
    return None, None


def _resolve_axis(axis: str, coa_entry: Optional[dict], sanitized: Optional[str],
                  rules: List[dict]) -> Tuple[Optional[str], str, Optional[str], Optional[float], str]:
    """Resolve one classification axis.

    Returns (code, method, rule_id, confidence, rationale_fragment).
    """
    key = "sha" if axis == "SHA" else "srhr"
    if coa_entry is not None and coa_entry.get(key) is not None:
        return (coa_entry[key], "coa_map", f"coa_map:{key}", 0.95,
                coa_entry.get("rationale") or "chart-of-accounts mapping")
    code, rule = _keyword_match(axis, sanitized, rules)
    if rule is not None:
        return (code, "keyword", rule["id"], float(rule["confidence"]),
                f"keyword rule '{rule['id']}' matched sanitised description")
    reason = ("account code has no mapping in this axis" if coa_entry is None or coa_entry.get(key) is None
              else "no rule matched")
    return (None, "unresolved", None, None, reason)


def classify(df: pd.DataFrame, rules: dict,
             flagged_ids: Optional[set] = None) -> pd.DataFrame:
    """Classify every record; one row per record covering both axes.

    ``flagged_ids`` are record_ids carrying high-severity data-quality
    flags (e.g. adversarial text, label mismatch). They are still
    classified by rule precedence but routed to the review queue.
    """
    coa_maps = rules.get("coa_maps", {})
    kw_rules = rules.get("keyword_rules", [])
    version = str(rules.get("rule_version", "0"))
    flagged_ids = flagged_ids or set()
    rows: List[Dict] = []

    for rec in df.itertuples(index=False):
        entry = coa_maps.get(rec.country_code, {}).get(str(rec.account_code))
        sanitized = getattr(rec, "description_sanitized", None)
        if not isinstance(sanitized, str):
            sanitized = rec.description if isinstance(rec.description, str) else None

        sha, sha_m, sha_r, sha_c, sha_why = _resolve_axis("SHA", entry, sanitized, kw_rules)
        srhr, srhr_m, srhr_r, srhr_c, srhr_why = _resolve_axis("SRHR", entry, sanitized, kw_rules)

        rationale_bits = [f"SHA: {sha_why}", f"SRHR: {srhr_why}"]
        adversarial = detect_adversarial(rec.description) is not None
        needs_review = (
            sha is None or srhr is None
            or rec.record_id in flagged_ids
            or adversarial
        )
        if adversarial:
            rationale_bits.append("adversarial text present; classified via account code only")
        if entry is not None and (sha is None or srhr is None):
            rationale_bits.append(entry.get("rationale") or "")

        rows.append({
            "record_id": rec.record_id,
            "sha_code": sha,
            "sha_method": sha_m,
            "sha_rule_id": sha_r,
            "sha_confidence": sha_c,
            "srhr_code": srhr,
            "srhr_method": srhr_m,
            "srhr_rule_id": srhr_r,
            "srhr_confidence": srhr_c,
            "rule_version": version,
            "review_status": "needs_review" if needs_review else "auto_classified",
            "rationale": "; ".join(b for b in rationale_bits if b),
        })
    return pd.DataFrame(rows)
