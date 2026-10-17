# Source log audit

Audited 2026-10-17. Structure only: record types, field names, tool names, and counts. No prompts, code, or file contents were copied.

## Summary

Both agents record enough edit evidence for verified attribution in their recent versions, and both need care around usage accounting.

| | Claude Code | Codex |
| --- | --- | --- |
| Sample | 1 transcript, 486 records | 13 rollouts, 7,976 records, 36 MB |
| CLI versions seen | 2.1.285, 2.1.302 | 0.94.0-alpha.10 → 0.153.4 (8 versions) |
| Best edit evidence | `toolUseResult.originalFile` + `structuredPatch` (numbered hunks) | `item_completed` → `FileChange.changes[path].unified_diff` (numbered hunks) |
| Before-state available | Yes, full original file text | No blob hash; reconstruct from Git or prior edits |
| Success signal | Tool result present, `is_error` not true | `FileChange` is emitted only on success; patch output exit code |
| Paths | Absolute `filePath` | Absolute keys in `FileChange`; patch text mostly relative (170 of 192) |
| Ordering | `parentUuid` chain + `timestamp` | `ordinal` strictly increasing in every file; ms UTC timestamps |
| Usage | Per API response, repeated on every record of that response | Cumulative `total_token_usage` + per-call `last_token_usage`; newer `token_usage_record` per turn |
| Subagents | `isSidechain` flag | `session_meta.parent_thread_id` + `subagent_history_start_ordinal` |

The structural audit used a cloud Claude Code transcript. Desktop transcripts in `~/.claude/projects` were later read in a real scan (see below) without malformed records.

## Codex

### Record envelope

Every line is `{timestamp, ordinal, type, payload}`. Types seen: `session_meta`, `turn_context`, `response_item`, `event_msg`, `token_usage_record`, `world_state`, `compacted`.

- `session_meta`: `session_id`, `cwd`, `cli_version`, `originator`, `source`, `model_provider`, `git {commit_hash, branch, repository_url}` (3 of 13), `parent_thread_id` and `subagent_history_start_ordinal` on subagent threads (6 of 13).
- `turn_context`: `cwd`, `model`, `effort`, `sandbox_policy`, `approval_policy`, `workspace_roots`, `turn_id`. Present every turn, so the effective cwd and model can change per turn. All 13 sample sessions stayed in one cwd.

### Edit evidence, by era

| Era | Versions | How edits appear | Evidence quality |
| --- | --- | --- | --- |
| Patch text only | 0.94, 0.98 | `custom_tool_call` `apply_patch`, `input` = `*** Begin Patch` text. Output is `Exit code: N` text or JSON `{metadata: {exit_code}}` | Context-anchored hunks without line numbers. Needs a known before-state to place |
| Patch + FileChange | 0.144 | Same tool call, plus an `item_completed` `FileChange` item per call | Numbered unified diff per file; strong |
| FileChange via code cells | 0.144, 0.153 | `exec` code cells call apply_patch internally; `FileChange` follows | Strong, but linked by order, not ID |

`FileChange.changes` is a map from absolute path to `{type: update, unified_diff, move_path}`, `{type: add, content}`, or `{type: delete}`. Diffs have numbered `@@` hunks but no `index` line, so there are no blob hashes to anchor against.

Linking `FileChange` to its call:
- 58 of 137: `item.id` equals the `apply_patch` `call_id`.
- 79 of 137: `item.id` uses a different ID form and matches no call. Each followed an `exec` call. These must be paired by source order within the turn, with the pairing recorded as an inferred link.

Failures: 3 `apply_patch verification failed` outputs. None produced a `FileChange`. Treat `FileChange` as the success record, and a patch call without one as attempted/failed.

### Shell activity

112 `shell_command` calls and 43 `CommandExecution` items (with `exit_code`, `cwd`, `parsed_cmd`). Shell-driven file changes have no diff and stay unattributed unless Git evidence ties them to the session.

### Usage

`event_msg/token_count.info` holds both `total_token_usage` (cumulative) and `last_token_usage` (per call), with `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens`, `total_tokens`. Newer versions add `token_usage_record` per turn. Rules for the adapter:
- Use `last_token_usage` increments; use cumulative totals only to detect resets and gaps.
- `cached_input_tokens` is a subset of `input_tokens`; don't add them.
- Subagent threads may start with a copied history prefix (`subagent_history_start_ordinal`); skip inherited records.
- `compacted` records summarize earlier context; they are not new work.

## Claude Code

### Record envelope

Lines carry `type` (`user`, `assistant`, `attachment`, `system`, plus bookkeeping types `queue-operation`, `last-prompt`, `mode`, `cost-state`, `atis-latch`), and conversational records carry `uuid`, `parentUuid`, `sessionId`, `timestamp`, `cwd`, `gitBranch`, `version`, `isSidechain`.

### Edit evidence

Edits are `tool_use` blocks (`Write {file_path, content}`, `Edit {file_path, old_string, new_string, replace_all}`) answered by a `tool_result` in the next user record. That user record also carries `toolUseResult`:

- `Write`: `{type: create|update, filePath, content, originalFile, structuredPatch, userModified}`. `originalFile` is `null` for new files.
- `Edit`: `{filePath, oldString, newString, replaceAll, originalFile, structuredPatch, userModified}`.
- `structuredPatch[]`: `{oldStart, oldLines, newStart, newLines, lines}`.

With `originalFile` and a numbered patch, the before and after states are fully reconstructable from the log alone, which meets the bar for verified attribution (see [attribution.md](attribution.md)) without guessing a baseline. `userModified` flags edits the user changed during approval; those spans should not be credited to the agent unchanged.

Not seen in the audited sample: `MultiEdit`, `NotebookEdit`. Records of those tools are kept but produce no edits yet.

### Usage

Usage sits on `assistant` records in `message.usage` (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, …). One API response is split across several records (one per content block), and **each record repeats the same usage**: 43 of 52 responses in the sample had multiple records, all with identical usage. Deduplicate by `requestId` before summing, or totals inflate several times over.

## Decisions

1. **Codex adapter targets the FileChange path first.** Patch-text-only sessions (0.94–0.98) have no line numbers; attribution locates each hunk by content and treats it as verified only when the location is unique (see [attribution.md](attribution.md)).
2. **Claude Code adapter uses `toolUseResult`**, not tool inputs. Without `toolUseResult` an edit is `attempted`, never `succeeded`.
3. **Usage:** dedupe Claude Code by `requestId`; use Codex per-call deltas.
4. **Fixtures:** build synthetic fixtures that mirror these shapes (no real content), one family per era above.
5. **Desktop transcripts** are checked with `tandem audit`, which prints structure and counts only, so results can be shared without exposing content.

## Adapter validation against real logs

Run 2026-10-17 with adapters 0.1.0, counts only.

| | Codex (13 rollouts) | Claude Code (1 transcript) |
| --- | --- | --- |
| Malformed records | 0 | 0 |
| Edits recovered | 357 | 56 |
| From numbered diffs | 272 `FileChange` (101 linked by call ID, 171 by source order) | 56 `toolUseResult` |
| From patch text | 85 (0.94-era sessions, unnumbered hunks) | — |
| Failed edit calls | 3 apply_patch failures, no edits emitted | 0 failed edits |
| Reconstruction check | — | 7 of 7 updates rebuild exactly from `originalFile` + patch |
| Subagent prefix records skipped | 6 sessions | — |

Takeaways:
- Nearly half of Codex edits (171 of 357) are linked to their tool call only by position in the log, because they ran inside `exec` code cells. They are tagged `linked_by_source_order` so attribution can treat that link as weaker than an ID match.
- `exec` cell outputs carry no exit code, so their status is `unknown`; the `FileChange` items that follow them are the success evidence.

## Scan validation against real logs

Run 2026-10-17 with schema v2. The 13 Codex rollouts were scanned against empty stand-in repositories created at each project path the logs reference (no real code involved), plus this repository against its own Claude Code session log.

| Check | Result |
| --- | --- |
| Codex edits placed in a repository | 357 of 357, each in exactly one repository |
| Edits made outside the session's working directory | 251, from one session working in a different folder; placed in the repository actually edited |
| Claude Code edits to this repository | 61, all reconstructable from the log |
| Full scan, 13 rollouts (36 MB) | about 0.6 s per repository |
| Rescan with nothing changed | about 0.14 s |

This run also found a storage bug, now fixed: a multi-file `FileChange` yields several events sharing one provider ID, and a unique index on that ID silently dropped all but the first file. Migration 0002 makes the index non-unique, and inserts now skip only on an `event_id` conflict.

## First run on a real project

Run 2026-10-17 on a personal project (66 first-parent commits over two months) on macOS, with 144 desktop Claude Code transcripts and 13 Codex rollouts.

| Check | Result |
| --- | --- |
| Logs read | 144 Claude Code, 13 Codex; no malformed records |
| Codex edits placed | 251, from one session started in a different folder |
| Verified lines | 3,943 of 25,159 inserted lines (16%); 802 more supported |
| Codex survival at 5 commits | 3,798 / 3,943 = 96.3%; later removals were by the same session (605 lines) or unattributed changes (183) |
| Claude Code sessions in the project | 0 |

The project had been built with both agents, but no Claude Code transcript for it survived: Claude Code deletes transcripts after 30 days by default (`cleanupPeriodDays`), and the project's last commit was older than the oldest remaining transcript. That is why 81% of lines are unexplained. Tandem now warns when an agent's logs start after the analyzed history, and the README recommends raising the retention period.

