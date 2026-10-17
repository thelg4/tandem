# Metric definitions

Tandem reports independent measurements, not a composite score. Every value carries its numerator, denominator, horizon, eligible cohort, confidence filter, and exclusion counts. Aggregate rates pool counts; they never average per-session percentages.

Status values: `complete`, `partial`, `unavailable`, `pending`, `not_applicable`. A missing value is never shown as zero.

## Cohorts and maturity

For each attributed inserted or replaced line instance, `c0` is its first eligible commit on the selected first-parent history. Its evaluation endpoint is `c0 + N` first-parent commits (default N = 5). A cohort is **mature** only if that endpoint exists. Younger work is **pending**, not 100% successful.

## Definitions (v1)

| Metric | Definition |
| --- | --- |
| Survival at N commits | Mature line instances continuously retained through their N-commit endpoint ÷ all mature verified introduced line instances. A deleted line that is later reintroduced is a new instance. |
| Churn received | Count of verified cross-agent interactions, plus unique original line instances affected at least once. Cohort churn rate = mature instances changed by the other agent before their endpoint ÷ mature introduced instances. |
| Exact revert rate | Mature change groups fully restored to their known prior state by the other agent before the group endpoint ÷ mature change groups with an observable preimage. Partial reverts are reported separately. |
| Tokens per surviving line | Known usage for fully observable, fully matured session cohorts ÷ their surviving attributed line instances, shown with usage completeness and attribution coverage. |

A **change group** is one successful edit to one file anchored to one commit.

## Defaults

| Setting | Default |
| --- | --- |
| Survival horizon | 5 first-parent commits |
| Recent-interaction highlight | 24 hours **or** 5 commits |
| Evidence filter | verified only |
| Retention | exact text after line-ending normalization |

Comparative highlighting is suppressed below 30 mature lines or 3 contributing sessions. These are presentation defaults, not statistical significance thresholds.

## Worked example (synthetic)

A Claude session contributes 100 verified line instances in 10 change groups at C0. By C5, 70 remain intact. Codex affected 20 distinct instances in 3 interactions, including one complete exact revert; an unknown actor changed the other 10.

| Output | Result |
| --- | --- |
| Survival at 5 | 70 / 100 = 70% |
| Churn received | 20 / 100 = 20%; 3 interactions |
| Exact revert rate | 1 / 10 = 10% (already inside the interaction count) |
| Tokens per surviving line | 14,000 / 70 = 200, if usage is complete |

This demonstrates the measurement rules, not a finding about either agent. Rewrites may be improvements, and code that survives may still be wrong.
