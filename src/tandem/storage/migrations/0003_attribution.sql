-- Attribution results per analysis run.

ALTER TABLE lineage ADD COLUMN state TEXT NOT NULL DEFAULT 'present';
    -- present | removed | dropped_before_commit | provisional | unavailable
ALTER TABLE lineage ADD COLUMN origin_kind TEXT NOT NULL DEFAULT 'unknown';
    -- agent | unknown | baseline
ALTER TABLE lineage ADD COLUMN reason_codes TEXT;
CREATE INDEX lineage_edit ON lineage(run_id, introduced_edit_id);
CREATE INDEX lineage_removed_by ON lineage(run_id, removed_edit_id);

CREATE TABLE edit_outcomes (
    run_id               INTEGER NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    edit_id              TEXT NOT NULL REFERENCES edits(edit_id),
    outcome              TEXT NOT NULL,
        -- committed | overwritten_before_commit | uncommitted | no_effect | unplaced
        -- | before_history | excluded
    evidence_class       TEXT,
    anchor_commit_id     TEXT,
    lines_inserted       INTEGER NOT NULL,
    lines_committed      INTEGER NOT NULL,
    lines_removed        INTEGER NOT NULL,
    reason_codes         TEXT,
    PRIMARY KEY (run_id, edit_id)
);

CREATE TABLE run_attribution (
    run_id                   INTEGER PRIMARY KEY REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    baseline_commit_id       TEXT,           -- NULL when history starts at the root commit
    commits_analyzed         INTEGER NOT NULL,
    history_truncated        INTEGER NOT NULL,
    tracked_files            INTEGER NOT NULL,
    tracked_inserted_lines   INTEGER NOT NULL,
    untracked_inserted_lines INTEGER NOT NULL,
    unavailable_paths        TEXT,           -- JSON {path: reason}
    rename_policy            TEXT NOT NULL,
    diff_policy              TEXT NOT NULL,
    rules_version            TEXT NOT NULL
);
