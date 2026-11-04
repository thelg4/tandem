-- Durability scorecard context per run and scope. Metric values live in metrics.

CREATE TABLE scorecards (
    run_id           INTEGER NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    scope_kind       TEXT NOT NULL,          -- agent | session
    scope_id         TEXT NOT NULL,
    agent            TEXT NOT NULL,
    horizon_commits  INTEGER NOT NULL,
    mature_lines     INTEGER NOT NULL,
    pending_lines    INTEGER NOT NULL,
    sessions         INTEGER NOT NULL,
    comparable       INTEGER NOT NULL,       -- presentation threshold, not significance
    usage_notes      TEXT,                   -- JSON {session: reason} for excluded usage
    PRIMARY KEY (run_id, scope_kind, scope_id)
);
