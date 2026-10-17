# Interaction rules (v0.1.0)

An interaction is a later agent edit changing lines that an earlier edit from a different session wrote. Tandem derives interactions from attribution (see [attribution.md](attribution.md)), so they inherit its evidence: a change only counts if the later edit was rebuilt from its log and its removal actually reached a commit, or removed lines that were never committed.

## What counts

- **Cross-agent:** Claude Code changed Codex's lines, or the reverse. These are the headline numbers.
- **Same agent, different session:** kept and tagged `same_agent_cross_session`, counted separately.
- **Same session:** a session revising its own lines is not an interaction.
- Lines removed by an unexplained change (a hand edit, a formatter, a shell command) are not interactions; the report shows them as removed by an unattributed change.
- A removal that is undone before the commit is not an interaction.

One interaction covers one contiguous change by the later edit. A later edit that changes the earlier edit's lines in three places produces three interactions, each listing the lines it affected.

## Labels

Every interaction has exactly one primary label, so headline counts never overlap.

| Label | Rule |
| --- | --- |
| `exact_revert` | The later change removed exactly the lines the earlier change inserted and put back exactly the lines it replaced. Deleting an added block counts, since it restores the earlier state. |
| `partial_revert` | The later change put back only text the earlier change replaced, but not an exact restoration. |
| `removal` | The later change deleted the lines without a replacement. |
| `rewrite` | The later change replaced the lines with something else. |
| `overlap_candidate` | Either edit's evidence is ambiguous, so order or lineage is uncertain. The underlying shape is kept as a tag. Excluded from verified totals. |

Labels describe what changed, not intent or correctness. A rewrite may be an improvement; an exact revert may be the right call. Commit messages and prompts are not used to judge either.

## Evidence

An interaction's evidence class is the weaker of the two edits' classes: verified only when both edits are verified.

## Timing and the highlight window

- **Elapsed time:** the later edit's timestamp minus the earlier edit's.
- **Commit distance:** first-parent commits from where the earlier lines landed to where the removal landed. Unknown when the earlier lines were never committed (tag `before_commit`).
- **Recent:** within 24 hours OR 5 commits by default (`window_hours`, `window_commits`, `window_mode`: `either`, `both`, `hours`, `commits`). An unknown time or distance never satisfies a predicate. The window only highlights; older interactions are still detected and counted.

## Review order

Interactions are listed for inspection in this order: cross-agent first, then exact and partial reverts, then most lines affected, then most recent. This is a review order, not a severity ranking.

## Inspecting one

`tandem explain <id>` shows both sessions, tools, times, commits, how each edit was tied to its tool call, where each log record is, and both edits' diffs read back from the original logs. IDs can be shortened to a unique prefix. `tandem session <id>` lists the interactions a session caused and received.

## Not yet covered

- `conflict_observed` (a merge or patch conflict recorded in a log or the repository) is defined in the spec but not detected yet.
- Test-associated repair labels need imported test results (P1).
