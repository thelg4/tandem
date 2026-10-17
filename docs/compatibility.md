# Compatibility

Adapter support is recorded per agent CLI version and fixture family. Undocumented log formats are not a stable contract, so only versions validated against pinned, sanitized fixtures are listed as supported.

## Agent log formats

| Agent | CLI version | Adapter version | Supported edit tools | Status |
| --- | --- | --- | --- | --- |
| Claude Code | not yet audited | 0.0.0 | — | not implemented |
| Codex | not yet audited | 0.0.0 | — | not implemented |

## Platform

| Component | Requirement | Status |
| --- | --- | --- |
| Python | 3.11+ | tested in CI on 3.11–3.13 |
| Git | 2.31+ | checked by `tandem doctor` |
| macOS | current | tested in CI |
| Linux | current | tested in CI |
| WSL | — | not yet validated |
| Windows (native) | — | deferred |
