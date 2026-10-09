# Evaluation

The evaluation checks calculations, access controls and destructive actions before assessing report quality. The CLI and application integration are implemented and tested locally. An offline pass does not verify Gemini or BigQuery access.

## Reproduce the checks

Follow the [setup instructions](../README.md) from the repository root. The complete suite requires the development dependencies and the live SDK dependencies, because ADK graph tests substitute a local fake model inside the real framework.

On Windows, using the repository virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\retail-agent.exe --demo
```

With that environment activated, the equivalent commands are `python -m pytest` and `retail-agent --demo`. The demo uses synthetic transactions and a simulated planner/reporter. It requires no API key or cloud credentials. It is a walkthrough of the application flow, not an evaluation of Gemini's language understanding or BigQuery's SQL engine.

| Run | Evidence at this development stage |
| --- | --- |
| Saved-report service | 17 tests verified locally, including concurrent confirmations and rollback |
| Complete test suite | 305 tests and 5 subtests passed, zero skips, on Python 3.12.14 / Windows, 9 October 2026 (Streamlit installed) |
| Optional UI installation | Fresh environment installed `requirements-ui-lock.txt` and the editable package; Streamlit 1.65.0 starts and `pip check` passes |
| Streamlit browser walkthrough | Offline comparison, product follow-up, literal-title save/open, cancel/confirm deletion, customer ranking and monthly follow-up, PII refusal, new conversation and actor switch verified on loopback |
| Static checks and dependencies | Ruff passes; `pip check` reports no broken requirements |
| Scripted CLI demo | Verified: comparison, follow-up, save, pseudonymous customer ranking and monthly follow-up, PII refusal, preview, plain-yes refusal, cancel and explicit confirmation |
| Live Gemini stages | Real ADK planning and reporting passed with `gemini-3.5-flash-lite` after the structured-output compatibility correction |
| Live BigQuery calculations | Four table schemas and US location verified; all six metrics matched independent SQL for authorized products 1 and 2, explicitly all time |
| Live application service | One complete question → plan → parameterized BigQuery query → Gemini report passed with two model calls and no report fallback |
| Live CLI | Normal ADC-based startup and the same six-metric all-time analysis passed with a Gemini report |
| Live Streamlit | Products 1 and 2 combined, all time, six metrics: Gemini report accepted without fallback; metric cards, source table, definitions and save/open verified |
| Broader live scenarios | Real CLI follow-ups, comparisons, empty/suppressed results, recommendations and report lifecycle verified on 9 October; see the automated acceptance record below. Injected failures remain a separate test layer. |

The live calculations returned one approved product group and suppressed another with fewer than three purchasers. Each of the application and reference queries processed 13,418,810 bytes and recorded 41,943,040 billed bytes, within the 100 MB per-query cap. A separate product-1 query for 2025 returned no rows; that case was retained rather than silently broadening its period.

The initial live Python checks used a short-lived credential from an existing authenticated `gcloud` session, supplied directly to the test client. Google consent was then completed, ADC configured with the execution project's quota context, and the normal `retail-agent --live` CLI passed the same analysis. No credential was saved in source control. These checks establish a working analytical path for one case, not broad natural-language accuracy or production readiness.

Gemini initially rejected the legacy schema's `additional_properties` field, then rejected the full JSON schema with array bounds. The runner now sends JSON Schema with `minItems`/`maxItems` omitted only from the provider copy. ADK and application Pydantic validation retain every original array bound and reject excess queries, products and report findings. This behavior is covered by the installed SDK wire-format and ADK graph tests.

## Streamlit validation on 8 October 2026

The UI-enabled suite runs without real cloud calls. AppTest covers clarification replies,
independent browser state, no implicit analysis on rerun or mode application, safe missing-live
configuration/provider errors, permission revocation, report reads, literal wildcard selection,
duplicate titles, expired/stale/superseded confirmations and reports deleted by another session.
Adapter checks retain SQLite's normal thread ownership and recheck permissions before returning
results. Chart tests preserve ratio cells, sort months, and reject ambiguous/nonfinite series.
Row-cap metadata is measured before group suppression, with an explicit possible-truncation notice.

A fresh Python 3.12.14 environment installed the frozen core and optional UI dependencies,
then the local package with `--no-deps -e .`. The CLI demo completed through explicit deletion.
The actual Streamlit app was inspected in the in-app browser at `127.0.0.1:8501` with separate
throwaway runtime data. Tables, bars, monthly series, titles and exact confirmation previews
were readable. North revenue was 900 across the fixture year; a selected customer contributed
30 in January and 60 in February; switching to south cleared prior content and returned 12,000
for the authorized lamp. An invalid actor removed prior results and left safe setup controls.
The test server was stopped afterward. This is a developer walkthrough, not a user study.

The audit's existing real-clock deadline assertion failed on floating-point subtraction
(`0.1000000000003638 <= 0.1`). It now uses an injected clock at exact binary fractions,
verifying decreasing remaining time, expiry and job cancellation without changing runtime logic.

The initial UI walkthrough used offline data; the later live check is recorded below.
General semantic attribution remains
limited by the numerical-token validator. SQL still applies its result limit before Python's
small-group suppression, so eligible groups can be omitted; the UI discloses possible truncation
but does not change that query policy. Authentication and deployment remain outside this local demo.

The independent whole-branch review identified three important issues, all corrected in one
regression-driven pass: request-starter replies could inherit an unrelated analysis, the
state/country region choice remained unresolved, and known confirmation tokens could be saved
as titles. Each was reproduced by failing tests before the fix. The final suite passed 255
tests and 5 subtests with zero skips; Ruff, dependency consistency and whitespace checks passed.
No critical or minor findings were reported. The fixes were verified by tests, without a second
review round. The explicit engine/live/production limitations above remain unchanged.

### Report validation and presentation correction

A reporter-only replay reproduced a false numerical rejection: Gemini correctly described
the approved minimum group size and zero suppressed groups, but those metadata numbers were
absent from the validator's allowlist. The check now includes typed, approved product scope,
the positive minimum customer threshold when privacy applies, and nonnegative suppressed-group
counts. Arbitrary operational statistics and invented numbers remain excluded. Regression
tests cover differing thresholds, invalid metadata, false claims and application integration.

Chat now presents a concise explanation and direct evidence-backed cards for one ungrouped
result. It moves exact source citations and tables to a closed source panel, and definitions
to a separate panel. Grouped results are never summed into headline totals. The deterministic
fallback uses readable sentences, omits generic advice and identifies itself. Scope, privacy,
query truncation and summary-subset notices remain visible. An independent review caught a
hidden summary-subset notice for 11 returned rows and 10 findings; a failing AppTest reproduced
it before the correction. Saved Markdown and underlying evidence remain complete.

The same user question was replayed in the actual Streamlit Live UI with
`gemini-3.5-flash-lite`: total revenue, orders, purchasing customers, units, average order value
and spend per customer for products 1 and 2 combined, all time, without grouping. It returned
237.00, 4, 4, 4, 59.25 and 59.25 respectively, without asserting a currency. Trace request
`4b96a9744031428e91f24f3fc68030c3` completed planning, BigQuery and reporting in 8.625 seconds,
with two model calls and no fallback event. The source table, scope panel and saving/reopening
the full report were checked in the browser. This is one live analytical case, not an
evaluation of every supported question. Numerical presence still cannot prove correct semantic
attribution of every value; changing the model does not remove that limitation.

### CLI presentation

The terminal now uses a compact answer panel and readable labels. A single ungrouped result
uses a vertical metric table; wide grouped records stack their fields without aggregating or
omitting rows. `/explain` and its case-insensitive/natural-language aliases return the approved
last report, sources and plan without new analytical calls. Fallback provenance is retained,
and changing permissions clears it with the cached report and evidence.

Rendering checks cover 50–120-column terminals, unchanged ratios and periods, empty results,
visible privacy/truncation notices, literal markup and full customer/report references.
Independent review found two ellipsis-truncation cases in narrow tables; both were reproduced
with failing tests, then corrected by folding text. Existing exact deletion previews and the
scripted CLI demo remain covered. A real CLI replay of the six-metric question above completed
with Gemini and BigQuery, without fallback, and returned the same six values. Its terminal
output was retained locally under ignored `runtime/live/cli-presentation` files.
The final suite passed 286 tests and 5 subtests with zero skips; Ruff and whitespace checks
passed. A piped interactive session verified the compact answer, uppercase `/EXPLAIN` and exit.

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

## Scenarios and expected results

Use fixture calculations as independent expected answers, rather than checking only that a generated query or report exists. The seeded fixture has 12 purchasers, two monthly orders each, and both product 1 and product 3 in every order. Product 3 costs 500 per item and is outside the north analyst's permissions.

For product 1 across January and February 2025, expect revenue 900, 24 orders, 12 purchasing customers, 24 units, scoped average order value 37.50 and spend per purchasing customer 75. January revenue is 300 and February revenue is 600. California contributes 360 and Texas 540 across both months. These amounts are synthetic and have no asserted currency. In particular, north-analyst revenue must exclude the 12,000 from product 3.

For customer spending in 2025, six customers each contribute 90 and six each contribute 60 within the permitted products. A top-five query returns five 90-valued rows, ordered before the limit; unauthorized product spending cannot affect their amounts or ranking. A selected 90-valued customer's monthly follow-up returns January 30 and February 60 using only its opaque reference.

| Scenario | Expected check | Evaluation layer |
| --- | --- | --- |
| Arithmetic and dates | Verify the fixture answers above, UTC month grouping, inclusive start and exclusive end, status exclusions and empty periods. | Compiler and gateway tests; manual SQL comparison in a live run |
| Mixed-product orders | Filter unauthorized items before summing revenue or counting scoped orders/customers. An explicit unauthorized product request fails. | Known-answer fixtures and scope tests |
| Plan injection | Reject unknown metrics, identifier dimensions, raw SQL and extra fields. Treat filter strings as parameter values. | Typed-contract and compiler tests |
| PII and small groups | Refuse identity/contact requests; omit raw customer IDs; suppress aggregate segments below the configured minimum. Explicitly approved pseudonymous individual analyses retain their scoped statistics. Reject contact patterns or identifier fields in returned evidence. | Adversarial fixtures, output checks and human review |
| Pseudonymous customers | Verify stable keyed labels, actor separation, metric ordering before LIMIT and scoped monthly follow-ups. Unknown, copied or revoked references cannot broaden a query. Raw IDs, source PII and the key are absent from model payloads, saved evidence, explanations and traces. | HMAC/gateway tests, fake BigQuery rows and recording-model integration |
| Identifier claims in reports | Raw-ID narrative and fabricated customer references trigger grounded fallback, even when the claimed number matches a valid metric. | Fake-reporter application regression tests |
| Report grounding | Reject unsupported numerical claims. Review whether supported numbers are attributed to the correct metric, segment and period, and whether findings cite their evidence. | Output tests plus analyst review |
| Multi-step and follow-up | Compare periods or segments using several bounded queries; a follow-up preserves prior dates/product filters unless explicitly changed. | Application integration and intent review |
| Empty-result correction | Permit at most one equivalent correction; preserve permissions and requested scope. Distinguish no data from privacy suppression, then explain the limitation. | Injected empty returns and integration tests |
| Syntax/schema failures | Stop safely on incompatible schemas. An erroneous compiled query cannot trigger unrestricted model SQL. Compiler defects require a code correction. | Injected gateway errors; live schema/query smoke check |
| Dependency failures and costs | Bound transient model retries, request deadlines, query count and byte budgets. Keep the CLI usable after timeout, access denial or outage. | Fake model/client failures and application tests |
| Partial comparisons | A later failed query cannot turn earlier evidence into a completed multi-query report. | Application regression test |
| Trace-file failures | Logging I/O errors cannot crash the chat or misreport a committed deletion; emit a diagnostic warning instead. | Application regression test |
| Saved reports | Check actor/conversation/mention selection; preview exact targets; require a separate confirmation; verify cancel, expiry, wrong actor/token and changed versions. | SQLite service tests and CLI walkthrough |
| Concurrent report actions | New reports survive an old preview. Overlapping or simultaneous confirmations cannot broaden deletion. A commit failure rolls back deletion and token consumption together. | Separate SQLite connections and forced transaction failures |
| Correlated traces | Link request, conversation, stage, evidence/query, report and deletion operation IDs; record outcomes, timings, retries and usage. Raw prompts, rows, report bodies and confirmation tokens must be absent. | Trace assertions using distinctive sensitive test markers |
| Accidentally pasted credentials | Mask configured API/pseudonym secrets and standard Google API-key patterns before model input/history. Reject credential-like evidence, model reports and saved titles. Verify absence from SQLite and traces. | 16 application regression cases using synthetic secrets; local boolean-only check of the configured API key |
| Opaque evidence citations | A verified citation with a digit-leading hash must not be treated as a business number. Its digits cannot authorize an invented amount; unknown or extended citations retain numeric checking. A fallback must be visible to the user. | 7 regression cases and a real CLI replay retaining the Gemini report |

Numerical-token validation is a limited check: a number present in the evidence can still be attached to the wrong fact, or described with the wrong unit. Contact-pattern detectors also do not prove that every possible personal detail is recognized. Credential redaction covers the configured secrets and standard Google API-key syntax; it is not a detector for every provider, token type or transformed representation. The application reduces exposure through approved aggregate fields, keyed customer pseudonyms and withholding raw identities; reviewer checks cover the remaining semantic gaps. Pseudonyms remain linkable. Empty/rejected-query correction is an equivalent-plan retry, not arbitrary SQL repair; compiler defects require source changes.

A live user run exposed a false rejection when the required citation `bq-7655e3b16480f29b` was scanned as the unsupported number `7655`. Only exact approved evidence IDs are now masked during numeric checking, after the contact, credential and identifier checks. A replay of the same six-metric product analysis with a ten-row limit retained the Gemini report and returned unchanged BigQuery facts. Other unverified reports still fall back, with an explicit message explaining that the generated summary could not be verified.

## Human review and live validation

Ask an analyst to evaluate whether each report answers the requested question, states metric definitions and scope, distinguishes observed contributions from causes, and gives action items supported by the evidence. For churn, require an agreed definition and observation window before accepting a result. A plausible narrative alone is insufficient.

Ask representative nontechnical users to find available analyses, clarify a missing period, narrow a follow-up, save a report and cancel or confirm deletion. Observe whether they understand synthetic-data labels, permissions, suppressed results and the exact deletion targets. Record task completion, mistakes and confusing wording; set performance targets from measured runs.

A live smoke run must separately verify the configured Gemini model, authentication, four dataset schemas, dataset location, parameterized SQL, dry-run estimates and actual job statistics. Compare a small approved query with independently computed SQL, then test the multi-step and follow-up cases. Preserve only sanitized evaluation evidence. Golden retrieval, preference learning and persona administration require separate evaluation before their proposed production implementation is released.
