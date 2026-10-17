"""Attribution: reconstruct tool edits against Git snapshots and propagate line lineage.

Pure rules over immutable evidence (spec §5, §6). Work packages B07 (days 5-6).

Contract:
- Exact content and local context identify spans; path and time only narrow candidates (ATT-01).
- Identical text at two positions is not the same line instance (ATT-02).
- A whole-file rewrite keeps provenance of unchanged lines (ATT-03).
- Ownership is stored per hunk/span, never transferred to untouched code (ATT-05).
- Retention is exact text after line-ending normalization only (ALG-06).
"""
