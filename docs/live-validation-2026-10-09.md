# Historical live validation — 9 October 2026

This record was imported from the Streamlit branch at commit `a160f9a` when merging the application and test cleanup. Counts of 299 or 305 tests below describe that branch **before the merge**, not the current battery. See [current evaluation](evaluation.md) for the unified suite.

The cloud runs were completed before the merge; the merge itself makes no new cloud-validation claim. Ignored transcripts and acceptance artifacts remain in the original Streamlit worktree's runtime directory. This document preserves the observed results and their limitations.

### Live acceptance checks on 2026-10-09

User-supplied CLI transcripts confirmed clarification followed by all-time revenue for products
1 and 2, a product breakdown reconciling to 543.50, an unauthorized-product refusal, a contact-data
refusal, a pseudonymous top-five ranking and one customer's monthly follow-up. Saving, reopening
after a CLI restart, exact-ID deletion, rejection of missing confirmation tokens/plain `yes`,
cancellation, and north/south report isolation were exercised. These are observed cases, not a
claim that every natural-language request or production authentication has been validated.

The south comparison of 2020–2023 with 2024–2026 returned 285.50/3 orders and 571.00/6 orders.
Its original trace recorded two successful real BigQuery queries and an `UnsafeOutput` reporting
failure. A reporter replay using their retained aggregate results reproduced a false rejection
of inclusive calendar endpoints: December 31 was absent from the numerical allowlist for an
exclusive January 1 end. The original rejected draft was not retained, so the replay establishes
a reproducible defect, not the exact wording of the original failure.

Validation now recognizes complete approved endpoint dates, including the preceding inclusive
day, without adding those new date components to the business-number allowlist. English, French,
ISO dates, leap days and rejection of unsupported dates/amounts are covered. Python also supplies
an inclusive `period_label` for Gemini to copy, avoiding a reproduced ambiguous before/through
formulation. Original evidence periods and query bounds are preserved. This still does not prove
the semantic correctness of arbitrary model prose.

A fresh full CLI Live run (`c05b100f66224f938b79270842db5f1e`) completed two BigQuery queries and
two Gemini calls with no fallback, correct inclusive wording and unchanged values. Output is
retained locally in ignored `runtime/live/comparison-cli-fixed.txt`. The full suite passed
299 tests and 5 subtests; Ruff and whitespace checks passed. An already running CLI must be
restarted to load the correction.

### Automated end-to-end acceptance on 2026-10-09

The acceptance driver operated real interactive CLI subprocesses through stdin/stdout, using
the configured Gemini model and ADC-backed BigQuery. Each run used an isolated `APP_DATA_DIR`;
existing user reports were not modified. Test confirmation tokens were redacted from retained
transcripts. These are real cloud calls, not offline fixtures. Numerical expectations below
describe the dataset observed that day and may change when the public dataset refreshes.

| Case | Observed result |
| --- | --- |
| Clarification, six metrics and product follow-up | Missing time prompted clarification; all six north metrics matched 543.50 / 9 / 9 / 9 / 60.39 / 60.39; product revenues reconciled to the total with the same scope and period. |
| Customer ranking and monthly follow-up | Five opaque references, sorted amounts of 69.50; selected customer's monthly revenue reconciled to its ranking amount. Unknown references did not execute queries. |
| Product permissions and PII | Unauthorized product and instruction-injection requests executed no query. Contact-data refusal called neither the model nor BigQuery. South could neither list nor open north reports. |
| Reports and process restart | Saved report reopened after a new CLI process. Preview, missing/wrong token, plain yes, cancellation, exact-ID confirmation, natural-language mention deletion and conversation deletion behaved as expected. Only isolated test reports were deleted. |
| Two-period comparison | Products 3/4 returned 285.50 / 3 orders and 571.00 / 6 orders for the complete requested periods. Date coverage was checked separately from totals. |
| Requested recommendations | Follow-up retained the comparison's dates/products and returned action items proposing investigation and comparable sub-periods, without asserting a proven cause. |
| Empty and privacy-suppressed results | January 2050 stayed empty without broadening dates or inventing zero revenue. Product 3 in 2020–2023 suppressed one small group and disclosed the suppression. |
| Schema, unsupported metric and reset | Four-table live schema available; undefined churn prompted clarification without a query; a new conversation cleared the analysis and prevented saving an old result. `/explain` made no provider calls. |
| Streamlit with real providers | 16 AppTest checks passed: Live setup, six metric cards, no analysis on rerun, save/open, exact preview, hidden token, cancel/confirm, PII refusal, actor change, comparison and reset. This exercises actual Streamlit callbacks and real cloud dependencies; it is not browser visual automation. The running server's HTTP health endpoint also passed. |
| Entry points and dependencies | Missing-key configuration exited safely before provider access; EOF emitted one session-ended message; offline scripted demo completed; dependency consistency and Ruff passed. |

The first broad CLI run exposed a planner defect: one exclusive end was `2023-12-31`, omitting
the requested final day, even though the available data yielded the same amounts. The service
now enforces recognized, fully specified English/French/ISO calendar ranges before querying:
it corrects the inclusive-to-exclusive conversion and rejects unrelated or omitted periods.
Six application regression cases cover this boundary. This limited parser does not guarantee
interpretation of every relative date, shorthand date or natural-language request.

The broader run also exposed a recommendations refusal. Planner/reporter instructions now
explicitly allow evidence-grounded next steps and preserve the prior analytical scope. The
same follow-up then passed in the actual CLI with action items. A temporary model failure on
the first empty-period run exhausted three reporter attempts and produced an explicitly
labelled factual fallback. A later replay obtained a Gemini explanation. Consequently, live
mode does not imply that a provider failure can never trigger the documented report fallback.

An independently authored BigQuery query using inclusive date predicates matched all six north
metrics, the top-five spending amounts, the south comparison and the empty future period.
Nine original application jobs were inspected for successful completion and correct product
parameters. The reference query processed 13,432,942 bytes. The final targeted CLI series
passed 41 checks across 12 interactions, seven BigQuery queries and ten model calls, with no
fallback. The full automated suite passed 305 tests and 5 subtests after the changes; injected
timeouts, quota/access errors, stale/expired confirmations, transaction failures and concurrent
deletions are covered there, rather than being represented as real external outages.

Sanitized local artifacts are under ignored `runtime/acceptance-20261009/`:
`cli-105304/acceptance.json` records the initial broad run and its failures;
`cli-105304/bigquery-audit.json` holds the independent aggregate comparison;
`cli-110207/acceptance.json` records the final targeted CLI pass;
`streamlit-105929/acceptance.json` records the 16 passing Live UI checks;
`entrypoints.json` records setup/exit/health checks. The acceptance scripts are retained beside
those artifacts. Previously observed cosmetic citation variants and Windows interrupt-specific
rendering were not fixed by this work; automated EOF/normal exits passed.
