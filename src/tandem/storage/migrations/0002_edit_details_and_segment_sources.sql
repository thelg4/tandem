-- Edit details for coverage reporting, and per-source session segments.

ALTER TABLE edits ADD COLUMN change_type TEXT;           -- add | update | delete
ALTER TABLE edits ADD COLUMN evidence_source TEXT;       -- claude_code.tool_use_result | codex.file_change | codex.patch_text
ALTER TABLE edits ADD COLUMN call_link TEXT;             -- call_id | source_order | none
ALTER TABLE edits ADD COLUMN move_to TEXT;
ALTER TABLE edits ADD COLUMN lines_added INTEGER;
ALTER TABLE edits ADD COLUMN lines_removed INTEGER;
ALTER TABLE edits ADD COLUMN user_modified INTEGER NOT NULL DEFAULT 0;

CREATE INDEX edits_event ON edits(event_id);
CREATE INDEX diagnostics_run ON diagnostics(run_id, code);

-- Segments are derived per source file, so a re-import replaces only that file's segments.
ALTER TABLE session_segments ADD COLUMN source_id TEXT REFERENCES sources(source_id);
CREATE INDEX session_segments_source ON session_segments(source_id, session_id);
