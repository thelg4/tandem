# Architecture

Tandem is a read-only pipeline. Three local input classes feed it: agent session logs, Git objects and history, and (later) test observations. Adapters normalize records, attribution reconstructs edit lineage, and analysis derives interactions and scorecards. A local SQLite database stores evidence and derived results. The CLI and the future web view read the same query layer.

```
Claude Code JSONL ─┐
Codex rollouts ────┼─► sources ─► adapters ─► attribution ─► interactions ─► metrics ─► storage ─► cli / web
Git objects ───────┘                              ▲
                                     git_reader ──┘
```

## Module boundaries

| Module | Path | Responsibility | Must not |
| --- | --- | --- | --- |
| sources | `src/tandem/sources/` | Discover log files under approved roots; bounded incremental JSONL reading | Do Git attribution or UI |
| adapters | `src/tandem/adapters/` | Parse provider records, pair tool calls and results, normalize usage into the event envelope | Leak provider formats downstream |
| git_reader | `src/tandem/git_reader.py` | Resolve revisions, walk first-parent history, read blobs and worktrees | Check out, fetch, run hooks, external diff or textconv |
| attribution | `src/tandem/attribution/` | Reconstruct edits against snapshots, match spans, propagate line lineage | Use time proximity as proof of authorship |
| interactions | `src/tandem/interactions/` | Rewrite, removal, exact-revert, overlap records | Infer intent or correctness |
| metrics | `src/tandem/metrics/` | Mature cohorts and pooled, versioned scorecards | Drop denominators or exclusions |
| storage | `src/tandem/storage/` | Migrations, transactional imports, run manifests, indexed reads | Overwrite the latest complete run with a failed one |
| cli | `src/tandem/cli.py` | Present findings, filters, evidence, diagnostics | Run commands found in transcripts |

Shared contracts live in `src/tandem/model/`: the event envelope, evidence classes, interaction labels, and the metric value type.

## Scan transaction model

1. Capture the target Git object ID and each source file's byte size at scan start. Records past that size belong to the next scan.
2. Import each bounded source snapshot in its own transaction.
3. Create an `analysis_run` from a fixed set of inputs. Derived rows become visible only when the run completes; `latest_run` points only at complete runs.
4. One writer at a time (file lock); readers are concurrent (SQLite WAL).

## Versioning

Adapters, attribution rules, interaction rules, metric definitions, the event envelope, the report format, and the database schema are versioned independently in `src/tandem/versions.py`. A parser change may require reimporting sources; a metric policy change reuses stored evidence and creates a new derived run.

## Storage location

One database per repository, outside the repository:

- macOS: `~/Library/Application Support/tandem/repos/<id>.sqlite3`
- Linux: `$XDG_DATA_HOME/tandem/repos/<id>.sqlite3` (default `~/.local/share`)

Override with `TANDEM_DATA_DIR` or `--db`.
