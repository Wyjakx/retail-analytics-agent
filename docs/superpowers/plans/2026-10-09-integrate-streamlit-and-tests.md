# Integrate Streamlit and the application test battery

**User instruction:** "merge le tout" after discovering that the 52-case cleanup and the Live/Streamlit application existed in separate worktrees.

**Target:** One locally merged `main` containing the complete application, Live fixes and meaningful automated battery. Update the retained Streamlit checkout to the same commit so its CLI cannot keep using the old suite. Preserve its ignored credentials, reports and historical acceptance artifacts. No push or additional live cloud calls are needed.

## Integration

- [x] Confirm the other chat is idle and inspect both worktrees; no unrelated tracked changes found.
- [x] Verify both baselines: cleanup 52 tests; Streamlit 305 tests and 5 subtests, Ruff passing.
- [x] Commit cleanup as `545622a` and Streamlit/Live changes as `a160f9a`; fetch origin, whose main remains `e828ebc`.
- [x] Merge Streamlit into the cleanup branch without committing; retain full production code and resolve old-test modify/delete conflicts in favor of the replacement battery.
- [x] Preserve the dated Live evidence in a historical document; distinguish old branch counts from current verification.
- [x] Retain useful UI/session, clarification and reporting regressions without restoring duplicate framework/spelling cases.
- [x] Verify actual merged CLI, Streamlit callbacks and core battery: 121 scenarios pass with zero skips; 12/12 in-memory effectiveness probes detected. Ruff, dependency and whitespace checks pass.
- [x] Complete an independent read-only integration review. Correct the probe's stale `_scope` method target to `refresh_scope`; re-review finds no remaining consequential integration issue. Source, entry point, dependencies and CI match `a160f9a`.
- [ ] Commit the merge, fast-forward local `main`, and fast-forward the existing Streamlit branch to the same commit.
- [ ] Confirm clean working trees, identical code in both directories and a passing final suite.

## Conflict decisions

- Production files from the Streamlit branch are preserved, including shared runtime/session construction, report reopening, formatting, evidence metadata, explicit-date enforcement and recommendation prompts.
- The cleanup replaces the old analytical/model/service/security test files. Their new consequential behaviors move into application scenarios: inclusive date conversion affects actual totals; wrong or missing comparison periods execute no query; `/open` respects ownership; `/explain` preserves fallback and current permissions; suppression does not hide a reached query limit.
- UI/session and report-grounding tests remain where they exercise unique behavior; redundant parameter spellings and duplicate layer checks are consolidated.
- Existing runtime data and local configuration stay in their respective worktrees. Branch integration changes tracked code, not user reports.
