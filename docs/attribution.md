# Attribution rules (v0.1.0)

Tandem attributes a line to an agent only when the agent's logged edit can be rebuilt exactly and the line it produced appears in a commit. Everything else stays unknown. This page describes the rules `tandem scan` applies; the version is recorded with every run.

## What is tracked

Every file an agent edited, from the start of the analyzed history to the target commit, as a list of **line instances**. An instance has an origin (an agent edit, an unexplained change, or the history baseline) and the first commit that contained it.

- History is the target's first-parent chain, newest 2,000 commits by default (`max_history_commits`), or from `--since`.
- Line identity is exact text after line-ending normalization. Indentation and whitespace changes count as changes.
- A modified line is a removal plus a new instance. Unchanged lines keep their instance, even when an agent rewrites the whole file.
- Exact (100%) renames carry lineage to the new path. Moves within a file and copies are not detected.
- Binary files, files over the blob size limit, and excluded paths (lockfiles, vendored and generated code) are not line-tracked. A tracked file that becomes binary or too large stops being tracked at that commit: its earlier agent lines keep their introduction record but are no longer reported present, and its later additions count as unknown.

## Applying an agent edit

The edit is rebuilt against the file's tracked working state:

| Evidence in the log | How it is applied |
| --- | --- |
| Claude Code original file + numbered patch | Before-state comes from the log itself |
| Numbered unified diff (Codex FileChange) | Applied at its line numbers with every context line checked; if the numbers don't fit, located by content |
| Codex patch text (no line numbers) | Located by content after its `@@` anchor |

If the log's before-state differs from the tracked state, something unlogged changed the file in between (a shell command, a formatter, a hand edit). That difference is applied first as an **unknown** change, so it is never credited to an agent.

A whole-file add that lands on a file with existing content has no logged before-state, so lines already on disk could have come from an unlogged change. Such an edit is capped at `supported`.

## Commits

Each commit's blob is compared with the working state. Matching lines keep their instance and are anchored to the commit. Lines only in the commit are new instances of unknown origin. Agent lines missing from the commit were **dropped before commit** and get no committed credit.

Edits are matched to the first commit at or after their timestamp. Commit dates only order candidates: an edit counts as committed only if its lines actually appear in the commit.

- Commit times have one-second resolution. An edit in the same second as a commit is applied before it only if the commit contains what the edit produced; otherwise it goes to the next commit.
- Edits from different sessions with the same timestamp are ordered so that each edit's logged state fits, when such an order exists.
- A removed line stays pending until the next commit that touches its file. If the same text is re-inserted **in the same place** before then (by an unlogged change, another edit, or the commit itself), the removal did not happen: the line keeps its instance and owner, and a removal-only edit is reported as overwritten before commit. "Same place" means directly after the line that preceded it; the same text appearing anywhere else is a new line.

## Evidence classes

| Class | When |
| --- | --- |
| verified | Exact, unique reconstruction, and the line appears in a commit |
| supported | Reconstruction found more than one possible location, or the user changed the edit during approval |
| ambiguous | The edit was based on a file state that another session had since replaced (concurrent edits) |
| unknown | No supported log explains the line |
| provisional | The line has not appeared in any analyzed commit |

Default metrics count verified lines only.

## Edit outcomes

`committed` (some inserted line, or a pure deletion, landed), `overwritten_before_commit`, `uncommitted`, `no_effect` (the write changed nothing), `unplaced` (could not be rebuilt; reason recorded), `before_history`, `excluded`.

## Coverage

- **Line coverage:** verified agent lines introduced in range ÷ all lines inserted in range. Lines in files no agent touched come from `git log --numstat`, so they count toward the denominator as unknown.
- **Edit coverage:** placed edits ÷ all successful edits observed in the repository.

These two measures are reported separately and must not be conflated.

## Known limits

- Moves made outside a logged edit (for example `mv` in a shell) are not followed; edits to the old path stay provisional.
- Identical lines at several positions are aligned by a deterministic diff (`difflib.SequenceMatcher`); a different alignment could assign some of them differently.
- Merge commits are measured against their first parent; side-branch history is not walked. Agent edits made on another branch are applied to the first-parent state and can inflate unknown and inserted counts when that work later merges.
- If an agent edits a file's new path after a `git mv` but before the rename is committed, the rename replaces that edit's working state and the edit is reported as overwritten.
