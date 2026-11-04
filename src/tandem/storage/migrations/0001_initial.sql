-- Tandem schema v1.
-- Git object IDs are opaque TEXT without an assumed hash length.
-- Timestamps are UTC TEXT with original precision, plus source ordering.
-- NULL means unknown and is never rendered as zero.

CREATE TABLE repositories (
    repo_id          TEXT PRIMARY KEY,           -- local UUID; no remote URL required
    common_git_dir   TEXT NOT NULL UNIQUE,
    display_name     TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE worktrees (
    worktree_id      TEXT PRIMARY KEY,
    repo_id          TEXT NOT NULL REFERENCES repositories(repo_id) ON DELETE CASCADE,
    path             TEXT NOT NULL,
    is_primary       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (repo_id, path)
);

CREATE TABLE sources (
    source_id        TEXT PRIMARY KEY,
    provider         TEXT NOT NULL,              -- claude_code | codex
    path             TEXT NOT NULL,
    generation       INTEGER NOT NULL DEFAULT 0, -- bumped when the source is rewritten
    prefix_digest    TEXT,
    processed_offset INTEGER NOT NULL DEFAULT 0,
    snapshot_limit   INTEGER,
    parser_version   TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('active', 'unavailable', 'rewritten', 'unsupported')),
    last_scanned_at  TEXT,
    UNIQUE (provider, path)
);

CREATE TABLE sessions (
    session_id          TEXT PRIMARY KEY,
    agent_family        TEXT NOT NULL,
    provider_session_id TEXT NOT NULL,
    parent_session_id   TEXT REFERENCES sessions(session_id),
    first_observed_at   TEXT,
    last_observed_at    TEXT,
    completeness        TEXT NOT NULL DEFAULT 'unknown',
    UNIQUE (agent_family, provider_session_id)
);

-- One session may touch several repositories.
CREATE TABLE session_segments (
    segment_id       INTEGER PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    repo_id          TEXT REFERENCES repositories(repo_id),
    worktree_id      TEXT REFERENCES worktrees(worktree_id),
    first_sequence   INTEGER NOT NULL,
    last_sequence    INTEGER NOT NULL
);

CREATE TABLE events (
    event_id            TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    source_generation   INTEGER NOT NULL,
    record_offset       INTEGER NOT NULL,
    record_length       INTEGER NOT NULL,
    provider_event_id   TEXT,
    session_id          TEXT REFERENCES sessions(session_id),
    sequence            INTEGER NOT NULL,
    event_type          TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('attempted', 'succeeded', 'failed', 'partial', 'unknown')),
    tool_name           TEXT,
    tool_call_id        TEXT,
    model_id            TEXT,
    repo_id             TEXT REFERENCES repositories(repo_id),
    worktree_id         TEXT REFERENCES worktrees(worktree_id),
    recorded_cwd        TEXT,
    effective_cwd       TEXT,
    relative_path       TEXT,
    path_resolution     TEXT NOT NULL,
    raw_timestamp       TEXT,
    utc_timestamp       TEXT,
    time_quality        TEXT NOT NULL,
    adapter_version     TEXT NOT NULL,
    envelope_version    TEXT NOT NULL,
    reason_codes        TEXT,                    -- JSON array
    UNIQUE (source_id, source_generation, record_offset, sequence)
);
CREATE INDEX events_session ON events(session_id, sequence);
CREATE INDEX events_repo ON events(repo_id, relative_path);
CREATE UNIQUE INDEX events_provider_id ON events(source_id, provider_event_id)
    WHERE provider_event_id IS NOT NULL;

CREATE TABLE usage_records (
    usage_id         INTEGER PRIMARY KEY,
    event_id         TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    basis            TEXT NOT NULL CHECK (basis IN ('per_call', 'cumulative', 'unknown')),
    category         TEXT NOT NULL,              -- raw provider meaning preserved
    raw_value        INTEGER NOT NULL,
    incremental      INTEGER,                    -- NULL when the increment cannot be verified
    segment          INTEGER NOT NULL DEFAULT 0  -- counter resets start a new segment
);
CREATE INDEX usage_event ON usage_records(event_id);

CREATE TABLE commits (
    commit_id        TEXT NOT NULL,
    repo_id          TEXT NOT NULL REFERENCES repositories(repo_id) ON DELETE CASCADE,
    tree_id          TEXT NOT NULL,
    parent_ids       TEXT NOT NULL,              -- JSON array, ordered
    author_time      TEXT,                       -- metadata only, not a causal clock
    committer_time   TEXT,
    PRIMARY KEY (repo_id, commit_id)
);

CREATE TABLE file_versions (
    repo_id          TEXT NOT NULL,
    commit_id        TEXT NOT NULL,
    path             TEXT NOT NULL,
    blob_id          TEXT,
    change_status    TEXT NOT NULL,              -- A M D R, or metadata-only
    previous_path    TEXT,
    excluded_reason  TEXT,
    PRIMARY KEY (repo_id, commit_id, path),
    FOREIGN KEY (repo_id, commit_id) REFERENCES commits(repo_id, commit_id) ON DELETE CASCADE
);

CREATE TABLE edits (
    edit_id              TEXT PRIMARY KEY,
    event_id             TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    repo_id              TEXT REFERENCES repositories(repo_id),
    path                 TEXT NOT NULL,
    before_digest        TEXT,
    after_digest         TEXT,
    reconstruction       TEXT NOT NULL,          -- reconstructed | partial | failed | unsupported
    anchor_commit_id     TEXT
);
CREATE INDEX edits_path ON edits(repo_id, path);

CREATE TABLE edit_spans (
    span_id              INTEGER PRIMARY KEY,
    edit_id              TEXT NOT NULL REFERENCES edits(edit_id) ON DELETE CASCADE,
    old_start            INTEGER,
    old_count            INTEGER,
    new_start            INTEGER,
    new_count            INTEGER,
    evidence_class       TEXT NOT NULL CHECK (evidence_class IN ('verified', 'supported', 'ambiguous', 'unknown', 'provisional')),
    candidate_sessions   TEXT,                   -- JSON array when ambiguous
    reason_codes         TEXT
);

-- Line-instance intervals, not one row per Python object per line.
CREATE TABLE lineage (
    lineage_id           INTEGER PRIMARY KEY,
    run_id               INTEGER NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    repo_id              TEXT NOT NULL,
    path                 TEXT NOT NULL,
    introduced_commit_id TEXT,
    introduced_edit_id   TEXT REFERENCES edits(edit_id),
    removed_commit_id    TEXT,
    removed_edit_id      TEXT REFERENCES edits(edit_id),
    line_start           INTEGER NOT NULL,
    line_count           INTEGER NOT NULL,
    owner_session_id     TEXT REFERENCES sessions(session_id),
    owner_agent_family   TEXT,
    evidence_class       TEXT NOT NULL,
    rule_version         TEXT NOT NULL
);
CREATE INDEX lineage_path ON lineage(run_id, repo_id, path);
CREATE INDEX lineage_owner ON lineage(run_id, owner_session_id);

CREATE TABLE interactions (
    interaction_id       TEXT PRIMARY KEY,
    run_id               INTEGER NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    original_edit_id     TEXT NOT NULL REFERENCES edits(edit_id),
    later_edit_id        TEXT NOT NULL REFERENCES edits(edit_id),
    path                 TEXT NOT NULL,
    affected_ranges      TEXT NOT NULL,          -- JSON
    primary_label        TEXT NOT NULL,
    tags                 TEXT,                   -- JSON array
    evidence_class       TEXT NOT NULL,
    elapsed_seconds      REAL,                   -- NULL when the clock is unknown
    commit_distance      INTEGER,                -- NULL when unknown
    rule_version         TEXT NOT NULL
);
CREATE INDEX interactions_original ON interactions(original_edit_id);
CREATE INDEX interactions_later ON interactions(later_edit_id);

CREATE TABLE test_observations (
    observation_id       INTEGER PRIMARY KEY,
    repo_id              TEXT NOT NULL REFERENCES repositories(repo_id),
    source               TEXT NOT NULL,
    commit_id            TEXT,
    tree_digest          TEXT,
    dirty                INTEGER,
    command              TEXT,
    environment_digest   TEXT,
    suite_digest         TEXT,
    collected_at         TEXT,
    outcomes             TEXT,                   -- JSON {test_id: outcome}
    completeness         TEXT NOT NULL
);

CREATE TABLE analysis_runs (
    run_id               INTEGER PRIMARY KEY,
    repo_id              TEXT NOT NULL REFERENCES repositories(repo_id),
    state                TEXT NOT NULL CHECK (state IN ('running', 'complete', 'partial', 'failed')),
    started_at           TEXT NOT NULL,
    finished_at          TEXT,
    target_commit_id     TEXT,
    baseline_commit_id   TEXT,
    policy_hash          TEXT NOT NULL,
    manifest             TEXT NOT NULL           -- JSON: inputs, fingerprints, versions
);

-- Only a completed run may become the latest pointer.
CREATE TABLE latest_run (
    repo_id              TEXT PRIMARY KEY REFERENCES repositories(repo_id),
    run_id               INTEGER NOT NULL REFERENCES analysis_runs(run_id)
);

CREATE TABLE metrics (
    run_id               INTEGER NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    scope_kind           TEXT NOT NULL,          -- agent | session | repo
    scope_id             TEXT NOT NULL,
    name                 TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('complete', 'partial', 'unavailable', 'pending', 'not_applicable')),
    numerator            INTEGER,
    denominator          INTEGER,
    horizon_commits      INTEGER NOT NULL,
    exclusions           TEXT,                   -- JSON
    definition_version   TEXT NOT NULL,
    PRIMARY KEY (run_id, scope_kind, scope_id, name)
);

CREATE TABLE diagnostics (
    diagnostic_id        INTEGER PRIMARY KEY,
    run_id               INTEGER REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    source_id            TEXT REFERENCES sources(source_id) ON DELETE CASCADE,
    severity             TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    code                 TEXT NOT NULL,
    message              TEXT NOT NULL,
    record_offset        INTEGER,
    excerpt              TEXT                    -- bounded, escaped
);

-- User notes live apart from machine-derived results.
CREATE TABLE annotations (
    annotation_id        INTEGER PRIMARY KEY,
    target_kind          TEXT NOT NULL,
    target_id            TEXT NOT NULL,
    label                TEXT,
    note                 TEXT,
    created_at           TEXT NOT NULL
);
