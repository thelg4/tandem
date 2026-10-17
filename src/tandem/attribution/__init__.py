"""Attribution: reconstruct tool edits against Git snapshots and propagate line lineage.

Pure rules over immutable evidence.

Contract:
- Exact content and local context identify spans; path and time only narrow candidates.
- Identical text at two positions is not the same line instance.
- A whole-file rewrite keeps provenance of unchanged lines.
- Ownership is stored per hunk/span, never transferred to untouched code.
- Retention is exact text after line-ending normalization only.
"""
