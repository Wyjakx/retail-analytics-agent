"""Defense in depth around a data interface that never returns individual identities."""

from __future__ import annotations

import json
import math
import re
from typing import Any


class UnsafeOutput(ValueError):
    pass


EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\d[\s.-]?){9,15}(?!\w)")
SENSITIVE_REQUEST = re.compile(
    r"\b(emails?|e-mails?|phones?|telephones?|téléphones?|adresses?|addresses?|postcodes?|zip codes?|"
    r"first.name|last.name|full.name|customer.id|user.id|client.id|nom des clients|"
    r"noms des clients|identify customers|identif(?:y|ier) les clients|street_address|"
    r"postal_code|latitude|longitude|ip_address)\b"
    r"|\b(?:customers?|clients?|users?)(?:['’]s?|\s+)?\s*names?\b", re.I
)
INDIVIDUAL_RANKING = re.compile(
    r"\b(top|best|highest|meilleurs?)\s+(?:\d+\s+)?(?:spending\s+)?(?:customers?|clients?|users?)\b"
    r"|\b(?:rank|ranking|classer|classement)\b.{0,50}\b(?:customers|clients|users)\b"
    r"|\b(?:customers|clients|users)\b.{0,50}\b(?:rank|ranking|classement)\b", re.I
)
FORBIDDEN_FIELDS = {
    "email", "first_name", "last_name", "name", "street_address", "postal_code",
    "latitude", "longitude", "user_id", "customer_id", "phone", "address", "ip_address",
}
AGGREGATE_FIELDS = {
    "month", "state", "country", "category", "product", "revenue", "orders",
    "purchasing_customers", "units", "average_order_value", "spend_per_customer",
}
NUMBER = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)*(?:%)?")


def sanitize_input(text: str) -> str:
    if len(text) > 4000:
        raise ValueError("Please keep questions under 4,000 characters.")
    text = EMAIL.sub("[REDACTED_EMAIL]", text)
    return PHONE.sub("[REDACTED_PHONE]", text)


def privacy_refusal(text: str) -> str | None:
    if SENSITIVE_REQUEST.search(text):
        return "I can analyze aggregated retail data, but cannot disclose personal identifiers."
    if INDIVIDUAL_RANKING.search(text):
        return (
            "Individual customer rankings are disabled pending clarification of the privacy policy. "
            "I can compare customer segments or purchasing-customer counts instead."
        )
    return None


def validate_text(text: str) -> None:
    if EMAIL.search(text) or PHONE.search(text):
        raise UnsafeOutput("Output resembles personal contact information.")
    if len(text) > 30_000:
        raise UnsafeOutput("Output exceeds the presentation limit.")


def validate_evidence(evidence: list[dict[str, Any]]) -> None:
    for item in evidence:
        for row in item.get("rows", []):
            if any(key.lower() in FORBIDDEN_FIELDS or key not in AGGREGATE_FIELDS for key in row):
                raise UnsafeOutput("A result contains a forbidden identifier field.")
            for value in row.values():
                if isinstance(value, str):
                    validate_text(value)
                if isinstance(value, float) and not math.isfinite(value):
                    raise UnsafeOutput("A result contains a non-finite number.")


def validate_report(report: Any, evidence: list[dict[str, Any]], plan: Any) -> None:
    """Reject unsupported direct numerical claims; derived statistics must enter evidence first."""
    content = "\n".join([
        report.title, report.summary, *report.findings, *report.action_items, *report.caveats,
    ])
    validate_text(content)
    allowed: set[float] = set()

    def add_number(value: Any) -> None:
        if isinstance(value, bool):
            return
        if isinstance(value, (int, float)):
            numeric = float(value)
            allowed.update((numeric, round(numeric, 0), round(numeric, 1), round(numeric, 2)))
        elif isinstance(value, str):
            for token in NUMBER.findall(value):
                try:
                    allowed.add(float(token.replace(",", "").rstrip("%")))
                except ValueError:
                    pass

    for item in evidence:
        for row in item.get("rows", []):
            for value in row.values():
                add_number(value)
    for spec in plan.queries:
        for period in (spec.start_date, spec.end_date):
            if period:
                add_number(period.isoformat())
        for product in spec.product_ids or []:
            add_number(product)
    for token in NUMBER.findall(content):
        try:
            numeric = float(token.replace(",", "").rstrip("%"))
        except ValueError:
            raise UnsafeOutput("An unverified numerical claim was generated.") from None
        if numeric not in allowed:
            raise UnsafeOutput("An unverified numerical claim was generated.")


def safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
