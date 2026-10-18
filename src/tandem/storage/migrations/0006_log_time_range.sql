-- Time ranges for spotting missing logs: when each source's records start and end,
-- and which commit times an analysis run covered. UTC ISO-8601 text; NULL if unknown.
ALTER TABLE sources ADD COLUMN first_record_at TEXT;
ALTER TABLE sources ADD COLUMN last_record_at TEXT;
ALTER TABLE run_attribution ADD COLUMN history_first_at TEXT;
ALTER TABLE run_attribution ADD COLUMN history_last_at TEXT;
