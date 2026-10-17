-- Cross-agent interactions per analysis run (spec §7, §12).
-- The v1 table was keyed by interaction_id alone and never written; interactions recur
-- across runs, so it is replaced by a table keyed by (run_id, interaction_id).

DROP TABLE interactions;

CREATE TABLE interactions (
    run_id               INTEGER NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    interaction_id       TEXT NOT NULL,
    original_edit_id     TEXT NOT NULL REFERENCES edits(edit_id),
    later_edit_id        TEXT NOT NULL REFERENCES edits(edit_id),
    path                 TEXT NOT NULL,
    original_agent       TEXT NOT NULL,
    later_agent          TEXT NOT NULL,
    primary_label        TEXT NOT NULL,
        -- exact_revert | partial_revert | rewrite | removal | overlap_candidate
    tags                 TEXT NOT NULL,          -- JSON array
    evidence_class       TEXT NOT NULL,
    lines_affected       INTEGER NOT NULL,
    start_line           INTEGER NOT NULL,
    block_removed        INTEGER NOT NULL,
    block_inserted       INTEGER NOT NULL,
    original_replaced    INTEGER NOT NULL,
    original_inserted    INTEGER NOT NULL,
    elapsed_seconds      REAL,                   -- NULL when either time is unknown
    commit_distance      INTEGER,                -- NULL when a line was never committed
    original_commit_id   TEXT,
    removal_commit_id    TEXT,
    recent               INTEGER NOT NULL,
    review_rank          INTEGER NOT NULL,
    rule_version         TEXT NOT NULL,
    PRIMARY KEY (run_id, interaction_id)
);
CREATE INDEX interactions_original ON interactions(run_id, original_edit_id);
CREATE INDEX interactions_later ON interactions(run_id, later_edit_id);

ALTER TABLE run_attribution ADD COLUMN window_hours REAL;
ALTER TABLE run_attribution ADD COLUMN window_commits INTEGER;
ALTER TABLE run_attribution ADD COLUMN window_mode TEXT;
