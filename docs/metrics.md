# Durability metrics (v0.1.0)

Tandem reports independent measurements, not a composite score. Every value carries its numerator, denominator, horizon, and exclusion counts. Aggregate rates pool counts across sessions; they never average session percentages. Metrics are computed per agent and per session from each scan's attribution run (see [attribution.md](attribution.md) and [interactions.md](interactions.md)).

Status values: `complete`, `partial` (some inputs excluded for data reasons, listed in exclusions), `pending` (nothing mature yet), `not_applicable` (zero denominator), `unavailable` (no usable input). A missing value is never shown as zero.

## Cohorts and maturity

A line instance's cohort commit `c0` is the first-parent commit where it first landed. Its endpoint is `c0 + N` first-parent commits (default N = 5, `--horizon-commits`). It is **mature** only if the endpoint is inside the analyzed history. Younger lines are **pending**, never counted as successes.

Only verified lines and edits count. Supported or ambiguous lines are reported as `not_verified` exclusions. Lines from the history baseline, lines never committed, and lines in files that stopped being line-tracked before their endpoint (`became_untracked`) are not in any cohort.

## Definitions

| Metric | Definition |
| --- | --- |
| Survival at N | Mature lines still present at their endpoint ÷ all mature lines. A line removed at or before `c0 + N`, by anyone, did not survive. A deleted line that comes back is a new instance. |
| Churn received | Mature lines the other agent removed at or before their endpoint ÷ mature lines. Shown with the count of verified cross-agent interactions received and the lines they affected (all, not just mature ones). |
| Exact revert rate | Mature change groups the other agent fully restored to their prior state at or before `anchor + N` ÷ mature change groups. Partial restorations are counted separately. |
| Tokens per surviving line | Processed tokens of eligible sessions ÷ their surviving mature lines. |

A **change group** is one successful edit to one file, anchored to the first-parent commit where its result landed. Pure deletions are change groups too. Every committed edit has an observable preimage, because it was rebuilt from its log against a known file state.

## Tokens

Processed tokens never count cache tokens twice:

- Claude Code reports input, cache-write, cache-read, and output tokens as separate categories; all four are summed.
- Codex reports cached input inside input and reasoning inside output; only input and output are summed.

A session is **eligible** when its usage is complete (every usage record has all of its agent's categories), it worked only in this repository, and all its verified lines have matured. Excluded sessions are listed with a reason (`usage_categories_missing`, `no_usage_recorded`, `session_also_worked_outside_repository`, `sessions_not_matured`). If eligible sessions spent tokens but none of their lines survived, the report shows the tokens without a ratio.

Tokens are as reported by each agent, not a bill. Dollar estimates are not computed.

## Reading the numbers

- Survival describes persistence, not correctness. Rewrites may be improvements, and code that survives may still be wrong.
- Line ratios penalize useful deletions, refactoring, tests, and exploration. Read them alongside the added and removed counts.
- Scorecards with fewer than 30 mature lines or 3 contributing sessions are marked **small sample: not for comparison**. This is a presentation default, not a significance test.
- Changing the horizon needs a new scan (`tandem scan --horizon-commits N`). `tandem report --horizon-commits N` refuses to relabel numbers computed with a different horizon.

## Worked example (synthetic)

A Claude session contributes 100 verified lines in 10 change groups at C0. By C5, 70 remain intact. Codex affected 20 distinct lines in 3 interactions, including one complete exact revert; an unknown actor changed the other 10. Codex adds new lines at C4. Evaluated at C5:

| Output | Result |
| --- | --- |
| Claude survival at 5 | 70 / 100 = 70% |
| Claude churn received | 20 / 100 = 20%; 3 interactions |
| Claude exact revert rate | 1 / 10 = 10% (already inside the interaction count) |
| Claude tokens per surviving line | 14,000 / 70 = 200, if usage is complete |
| Codex lines from C4 | pending: their endpoint is C9 |

This example is a test (`tests/test_metrics_compute.py`). It demonstrates the measurement rules, not a finding about either agent.
