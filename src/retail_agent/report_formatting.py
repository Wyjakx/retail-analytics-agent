"""Readable labels and sentences built exclusively from approved result cells."""

import math
import re
from typing import Any


METRIC_LABELS = {
    "revenue": "Revenue", "orders": "Orders", "purchasing_customers": "Purchasing customers",
    "units": "Units sold", "average_order_value": "Average order value",
    "spend_per_customer": "Spend per customer",
}
DIMENSION_LABELS = {
    "month": "Month", "state": "State", "country": "Country", "category": "Category",
    "product": "Product", "customer": "Customer",
}
COUNT_METRICS = {"orders", "purchasing_customers", "units"}
SUMMARY_SUBSET_NOTICE = "This summary displays a subset of the approved rows."


def without_citations(text: str, evidence: list[dict[str, Any]]) -> str:
    """Hide exact approved citations in prose; retain originals in detailed reports."""
    for item in evidence:
        label = item.get("evidence_id")
        if not isinstance(label, str) or not label:
            continue
        citation = rf"\[{re.escape(label)}\]|\((?:evidence_id:\s*)?{re.escape(label)}\)"
        text = re.sub(rf"\s*(?:{citation})", "", text)
    return text.strip()


def format_metric(metric: str, value: Any) -> str:
    if value is None:
        return "Not available"
    if type(value) not in (int, float) or not math.isfinite(value):
        return str(value)
    if metric in COUNT_METRICS and value == int(value):
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _join(parts: list[str]) -> str:
    if len(parts) < 2:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def describe_row(row: dict[str, Any]) -> str:
    parts = [f"{DIMENSION_LABELS[key]}: {value}." for key, value in row.items()
             if key in DIMENSION_LABELS]
    if "revenue" in row:
        parts.append(f"Revenue was {format_metric('revenue', row['revenue'])}.")
    counts = []
    for metric, singular, plural in (
        ("orders", "order", "orders"),
        ("purchasing_customers", "purchasing customer", "purchasing customers"),
        ("units", "unit sold", "units sold"),
    ):
        if metric in row:
            counts.append(f"{format_metric(metric, row[metric])} "
                          f"{singular if row[metric] == 1 else plural}")
    if counts:
        parts.append("The results include " + _join(counts) + ".")
    for metric in ("average_order_value", "spend_per_customer"):
        if metric in row:
            parts.append(f"{METRIC_LABELS[metric]} was {format_metric(metric, row[metric])}.")
    return " ".join(parts)


def total_row(evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Only an actual ungrouped result can supply headline metrics; never sum groups."""
    if len(evidence) != 1 or len(evidence[0].get("rows", [])) != 1:
        return None
    item = evidence[0]
    row = item["rows"][0]
    if set(DIMENSION_LABELS).intersection(set(row) | set(item.get("columns", []))):
        return None
    return row


def period_label(item: dict[str, Any]) -> str:
    period = item.get("period")
    if not isinstance(period, dict) or not {"start", "end_exclusive"}.issubset(period):
        return "Period not specified"
    start, end = period["start"], period["end_exclusive"]
    if start is None and end is None:
        return "All time"
    if start is not None and end is not None:
        return f"{start} (inclusive) → {end} (exclusive)"
    return "Period not specified"
