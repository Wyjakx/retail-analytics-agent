# Rebuild the application test battery

> **For agentic workers:** Use superpowers:executing-plans, writing-good-tests guidance, verification-before-completion and requesting-code-review.

**User goal:** Replace the 121-test suite with useful tests of the application. The user explicitly authorizes deleting and rebuilding it, rejects count-only pruning and asks for Superpowers.

**Design:** Test meaningful user outcomes through the real service, installed ADK, arithmetic and SQLite. Script only external generation. Own an independent relational dataset with literal answers. Add focused Google wire-contract, real SQLite transaction and actual CLI process tests. Prove representative regressions fail the new suite.

**Original scope (8 October):** Tests and testing documentation only in `codex/streamlit-design`; no product behavior, dependency changes, cloud calls, commit or push. No numerical target for the test count and no claim of equivalent branch coverage. **Superseded integration instruction (9 October):** the user requested merging all work. The cleanup and Streamlit corrections are now committed and being integrated into local `main`, following the [merge plan](2026-10-09-integrate-streamlit-and-tests.md).

## Implementation

- [x] Inspect the old suite and application contracts; reject another cosmetic pruning pass.
- [x] Build test-owned mixed-product orders: 500 authorized revenue, 12 orders, 6 customers, 14 items; exclude 12,000 unauthorized revenue.
- [x] Exercise real ADK typed output, service policy, calculation, correction, fallback and save/delete paths.
- [x] Delegate independent Google-boundary and SQLite-transaction contracts using the parallel-agent skill.
- [x] Add actual CLI subprocesses with isolated environment and runtime files.
- [x] Verify all new tests before removing the old files; remove obsolete fixtures.
- [x] Add optional fresh-process in-memory effectiveness probes; detect 11/11 deliberate defects with a passing baseline.
- [x] Rewrite evaluation documentation to distinguish what runs locally from live/semantic validation and to state omitted coverage.
- [x] Complete independent review for consequential lost protections and false positives.
- [x] Apply justified review corrections and run final verification: 52 tests pass, 11/11 in-memory regression probes detected, Ruff and `git diff --check` pass. Production files are unchanged.

## Useful-test criteria

- State a plausible regression and check an observable answer, authorization boundary, cost limit, recovery or persisted outcome.
- Use real components wherever a local deterministic path exists; never mock the arithmetic whose correctness is being asserted.
- Keep cloud wire tests because local arithmetic does not execute BigQuery SQL.
- Keep separate-connection transaction tests because one happy-path service call does not exercise races or rollback.
- Reject model-script mismatches rather than silently accepting a fallback caused by a bad test fixture.
- Do not hide unrelated scenarios in loops to make the count smaller.
- Keep assertion answers independent of production helpers and copied schema constants.

The old 182 → 164 → 121 pruning iterations are superseded by this reconstruction. They are not the design or verification evidence for the final battery.

Review corrections: verify the shared six-call model budget and next-turn recovery; cap BigQuery RPC/job deadlines after time spent in dry-run and stop submission after cancellation; extend existing privacy flows with an unconfigured Google-shaped key and a confirmation token pasted into an ordinary question. Independent re-review found no further actionable issue in those corrections.
