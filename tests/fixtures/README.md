# Fixtures

Two kinds of fixtures back the inference rules:

1. **Synthetic Git histories** with known ownership, built in tests or checked in as small scripts. These are ground truth.
2. **Sanitized real logs** from supported Claude Code and Codex versions, organized by agent and CLI version (for example `claude_code/<version>/`, `codex/<version>/`). These need manual labels before they count as ground truth.

Never commit raw transcripts. Before adding a real log, remove prompts, file contents unrelated to the test, absolute home paths, tokens and keys, and anything identifying private code.

Required fixture scenarios are listed in the project specification (verification and release acceptance).
