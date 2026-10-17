# Compatibility

Adapter support is recorded per agent CLI version and fixture family. Undocumented log formats are not a stable contract, so only versions validated against pinned, sanitized fixtures are listed as supported. See [log-audit.md](log-audit.md) for what each version records.

## Agent log formats

| Agent | CLI versions audited | Edit evidence | Adapter | Status |
| --- | --- | --- | --- | --- |
| Claude Code | 2.1.285, 2.1.302 (cloud session) | `toolUseResult.originalFile` + `structuredPatch` | 0.1.0 | adapter implemented; validated on 1 transcript; desktop CLI not yet audited |
| Codex | 0.144.0-alpha.4, 0.153.x | `FileChange` unified diffs | 0.1.0 | adapter implemented; validated on real logs |
| Codex | 0.94.0-alpha.10, 0.98.0 | `apply_patch` text only (no line numbers) | 0.1.0 | adapter implemented; supported-class evidence at best |
| Codex | 0.146–0.148 | sessions present, no edits in sample | — | needs edit samples |

## Platform

| Component | Requirement | Status |
| --- | --- | --- |
| Python | 3.11+ | tested in CI on 3.11–3.13 |
| Git | 2.31+ | checked by `tandem doctor` |
| macOS | current | tested in CI |
| Linux | current | tested in CI |
| WSL | — | not yet validated |
| Windows (native) | — | deferred |
