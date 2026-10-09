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

    This is deliberately not a general natural-language date parser. Relative dates,
    shorthand years and partially specified dates remain the planner's responsibility.
    """
    plain = "".join(c for c in unicodedata.normalize("NFKD", question.lower())
                    if not unicodedata.combining(c))
    ranges = []
    for match in RANGE.finditer(plain):
        start, last = _date(match["start"]), _date(match["end"])
        exclusive = match["operator"] in ("before", "avant") or bool(match["exclusive"])
        end = last if exclusive else last + timedelta(days=1)
        if start >= end:
            raise ValueError("Please provide a valid calendar date range.")
        ranges.append((start, end, last))
    if not ranges:
        return plan
    queries = []
    covered = set()
    for spec in plan.queries:
        matches = [(start, end) for start, end, last in ranges
                   if spec.start_date == start and spec.end_date in (end, last)]
        if len(set(matches)) != 1:
            raise ValueError("The proposed dates do not match your explicit periods. Please restate the requested ranges.")
        start, end = matches[0]
        covered.add((start, end))
        queries.append(spec.model_copy(update={"start_date": start, "end_date": end}))
    if covered != {(start, end) for start, end, _ in ranges}:
        raise ValueError("The proposed plan omits a requested period. Please restate the comparison.")
    return AnalysisPlan(queries=queries)
