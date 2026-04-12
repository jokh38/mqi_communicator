# Fraction Grouping Document Review

**Date:** 2026-04-12
**Reviewed:** `specs/2026-04-12-fraction-grouping-design.md`, `plans/2026-04-12-fraction-grouping.md`

---

## 1. Spec: `2026-04-12-fraction-grouping-design.md` (241 lines)

### Strengths

- Clear decision record table upfront (batch vs real-time, which fraction for simulation vs PTN, polling mechanism).
- Well-defined data structures with explicit field types and semantics.
- The four-state fraction status model (`complete`, `partial`, `pending`, `anomaly`) is simple and covers the key edge cases.
- Good separation: fractions are in-memory only, `fraction_index` on delivery records is the only DB addition.
- Dedicated fraction event log is a smart operational choice.
- The test plan section is well-scoped.

### Issues

1. **Anomaly-only edge case not specified.** Section 2 says "If no complete fraction and all windows closed -> use earliest partial + special log. Status = `"ready"`" but does not specify what happens if there are **only anomaly fractions**. The plan addresses this (returns `ready` with empty beam_jobs and pending_reason), but the spec is silent.

2. **`FractionTracker.check_pending` signature mismatch.** Section 3 shows `check_pending(settings)` but the plan correctly uses `check_pending(rescan, now=None)` with dependency injection. The spec is stale here.

3. **`prepare_beam_jobs` error handling omitted.** The spec says `prepare_beam_jobs -> returns result.beam_jobs` but does not mention that it should handle exceptions gracefully. The plan adds a try/except wrapper.

4. **Tracker does not need settings.** Section 6 says `check_pending(self.settings)` but the tracker only needs a `rescan` callback. Spec-to-plan mismatch.

### Verdict

Solid architectural spec. The decision record is the best part. Minor inconsistencies with the plan on the `FractionTracker` API surface.

---

## 2. Plan: `2026-04-12-fraction-grouping.md` (2329 lines)

### Strengths

- Exceptional step-by-step TDD discipline: every task writes failing tests first, implements, then verifies.
- Git worktree strategy (Task 0) provides isolation without stashing.
- Incremental commit strategy means each task is independently revertable.
- Task dependencies are clearly sequenced: models (1) -> DB (2) -> repo (3) -> module (4-8) -> refactor (9) -> integration (10-11) -> regression (12).
- The grouping algorithm (Task 5) handles midnight crossing, anomaly detection, and pending state correctly.
- `FractionTracker` uses dependency injection (`rescan` callback) to avoid circular imports.
- Spec coverage checklist at the end maps every spec section to tasks.

### Issues & Risks

#### BUG-1: Wrong `expected_beam_count` in tracker registration (Task 11, Step 11.5)

`_discover_beams` passes `expected_beam_count=len(result.fractions[0].delivery_folders)` to `fraction_tracker.register()`. This uses the **actual** (incomplete) folder count from the pending fraction as the expected count. Since the fraction is pending, `delivery_folders` is incomplete by definition. The value should come from the RT plan's expected beam count, not the pending fraction's folder list.

**Fix:** Pass the expected beam count from `prepare_case_delivery_data`'s result or derive it from the RT plan lookup that already occurred inside `_discover_beams`.

#### BUG-2: `_rescan` closure assumes folder name equals case ID (Task 11, Step 11.7)

The closure `case_id = case_path.name` assumes the directory name is the case ID. If this assumption is valid in the codebase it works, but it is fragile. The tracker's `PendingCase` already stores `case_id`, but the callback only receives `case_path`.

**Fix:** Either pass `case_id` through the `rescan` callback signature, or use a closure that captures `case_id` from the `PendingCase` object rather than deriving it from the path.

#### RISK-1: Task 9 is a single large refactor (~400 lines)

Task 9 replaces the entire `prepare_case_delivery_data` body (lines 201-374) in one shot. This is the highest-risk task in the plan.

**Recommendation:** Split into two sub-tasks:
- Sub-task 9a: Add the `group_deliveries_into_fractions` call and fraction-index tagging without changing the return type.
- Sub-task 9b: Restructure the return type from tuple to `CaseDeliveryResult` and update callers.

#### RISK-2: No tracker persistence across application restarts

If the application restarts, all tracked pending cases are lost. Neither spec nor plan addresses this. In a production environment where the app may restart or crash, pending cases would be silently abandoned.

**Recommendation:** Add a note or a follow-up task to persist `PendingCase` entries (e.g., to a simple JSON file or a SQLite table) and restore them on startup.

#### RISK-3: Missing test for `_rescan` closure in `main.py`

The integration test (`test_main_fraction_integration.py`) only tests `FractionTracker` in isolation. There is no test verifying that the `_rescan` closure in `main.py` correctly extracts `case_id` from `case_path.name`.

**Recommendation:** Add a unit test that patches `prepare_case_delivery_data` and verifies the `_rescan` closure passes the correct arguments.

#### ISSUE-1: Fallback `beam_number = 0` for missing PlanInfo data

`group_deliveries_into_fractions` uses `beam_number = 0` as a fallback when PlanInfo lacks a beam number (plan line 898). Folders without beam numbers still get counted toward the expected beam count, which could produce false anomaly flags.

**Recommendation:** Either skip folders without valid beam numbers or log a warning and exclude them from the fraction's beam count comparison.

#### ISSUE-2: Duplicate beam numbers within a fraction are not flagged

The spec states "Duplicate beam numbers within a fraction are flagged but do not split the fraction." The plan's implementation detects anomaly status (count > expected) but does not actually log a specific warning for duplicate beam numbers within a fraction.

**Recommendation:** Add a duplicate beam number check in `_close_current()` or in `group_deliveries_into_fractions` that calls `log_fraction_event` with a `"duplicate_beam_number"` event.

#### ISSUE-3: Inconsistent forward-reference string annotation

The plan uses `"CaseDeliveryResult"` as a forward reference string in `prepare_case_delivery_data`'s return type annotation. This works but is inconsistent with the rest of the module which does not use string annotations.

**Severity:** Cosmetic. No functional impact.

### Verdict

An impressively detailed and disciplined implementation plan. The TDD approach and incremental commits are best-practice. The main concerns are the `expected_beam_count` bug (BUG-1), the `_rescan` closure fragility (BUG-2), and the lack of tracker persistence across restarts (RISK-2).

---

## Summary Table

| Aspect | Spec | Plan |
|--------|------|------|
| Completeness | Good (minor gaps on edge cases) | Excellent |
| Accuracy | Minor API mismatches with plan | Two bugs identified |
| Testability | Clear test plan | Full TDD across 12 tasks |
| Risk management | N/A | Good (worktree, incremental commits) |
| Operational readiness | Missing restart recovery | Also missing |

## Recommended Actions Before Implementation

1. Fix BUG-1: correct `expected_beam_count` derivation in tracker registration.
2. Fix BUG-2: harden `_rescan` closure against folder-name != case-id.
3. Split Task 9 into smaller sub-tasks to reduce risk.
4. Add a follow-up task for tracker persistence across restarts.
5. Add ISSUE-2 fix: log duplicate beam numbers within a fraction.
6. Sync spec's `FractionTracker` API with the plan's dependency-injected design.
