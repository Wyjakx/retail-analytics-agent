"""Enforce unambiguous, fully specified calendar ranges before executing a plan."""

from datetime import date, timedelta
import re
import unicodedata

from .analytics import AnalysisPlan


MONTHS = {}
for names in (
    "january february march april may june july august september october november december",
    "janvier fevrier mars avril mai juin juillet aout septembre octobre novembre decembre",
):
    MONTHS.update({name: index for index, name in enumerate(names.split(), 1)})
MONTH = "(?:" + "|".join(MONTHS) + ")"
CALENDAR_DATE = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|{MONTH}\s+\d{{1,2}},?\s+\d{{4}}|\d{{1,2}}\s+{MONTH}\s+\d{{4}})"
RANGE = re.compile(
    rf"\b(?P<start>{CALENDAR_DATE})\s+(?P<operator>through|to|before|au|avant)\s+"
    rf"(?P<end>{CALENDAR_DATE})\b(?P<exclusive>\s*\(?exclus(?:ive|if|ivement)\)?)?"
)
YEAR = re.compile(r"\b(?:in|en|for|pour)\s+(20\d{2})\b(?![-\d])")
ALL_TIME = re.compile(
    r"\b(?:all[ -]?time|all years|ever|toutes les annees|depuis toujours|toute la periode)\b"
)
MIXED_RELATIVE = re.compile(
    r"\b(?:this month|last month|this year|last year|year[ -]to[ -]date|ytd|"
    r"up[ -]to[ -]date|ce mois|mois en cours|mois dernier|mois precedent|"
    r"depuis le debut de l.annee|a jour|jusqu.a aujourd.hui|cette annee|annee derniere)\b"
)


class DateConstraintError(ValueError):
    """An application-owned date limitation safe to display without provider text."""


def _date(value: str) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    parts = value.replace(",", "").split()
    if parts[0] in MONTHS:
        month, day, year = MONTHS[parts[0]], int(parts[1]), int(parts[2])
    else:
        day, month, year = int(parts[0]), MONTHS[parts[1]], int(parts[2])
    return date(year, month, day)


def enforce_explicit_periods(question: str, plan: AnalysisPlan) -> AnalysisPlan:
    """Correct only inclusive-end conversion; reject other conflicts without querying.

    Mixed comparisons may also contain a plain calendar year after in/en/for/pour,
    or an explicit all-time period. Mixed relative periods require full calendar
    ranges; other supplemental periods must match the bounded rules above. This
    is deliberately not a general natural-language date parser; questions without a full range
    retain the planner's existing date handling.
    """
    plain = "".join(c for c in unicodedata.normalize("NFKD", question.lower())
                    if not unicodedata.combining(c))
    ranges = []
    for match in RANGE.finditer(plain):
        start, last = _date(match["start"]), _date(match["end"])
        exclusive = match["operator"] in ("before", "avant") or bool(match["exclusive"])
        end = last if exclusive else last + timedelta(days=1)
        if start >= end:
            raise DateConstraintError("Please provide a valid calendar date range.")
        ranges.append((start, end, last))
    if not ranges:
        return plan
    # Years inside full dates are boundaries, not separately requested periods.
    remaining = RANGE.sub(" ", plain)
    if MIXED_RELATIVE.search(remaining):
        raise DateConstraintError(
            "Please restate mixed relative periods as full calendar ranges."
        )
    for match in YEAR.finditer(remaining):
        year = int(match[1])
        start, end = date(year, 1, 1), date(year + 1, 1, 1)
        ranges.append((start, end, end))
    if ALL_TIME.search(remaining):
        ranges.append((None, None, None))
    queries = []
    covered = set()
    for spec in plan.queries:
        matches = [(start, end) for start, end, last in ranges
                   if spec.start_date == start and spec.end_date in (end, last)]
        if len(set(matches)) != 1:
            raise DateConstraintError(
                "The proposed dates do not match your explicit periods. "
                "Please restate relative or partially specified periods as full calendar ranges."
            )
        start, end = matches[0]
        covered.add((start, end))
        queries.append(spec.model_copy(update={"start_date": start, "end_date": end}))
    if covered != {(start, end) for start, end, _ in ranges}:
        raise DateConstraintError("The proposed plan omits a requested period. Please restate the comparison.")
    return AnalysisPlan(queries=queries)
