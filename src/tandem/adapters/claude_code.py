"""Claude Code transcript adapter.

Reads JSONL transcripts, commonly under ~/.claude/projects. The parser contract
is established from sanitized fixtures of the installed CLI version, not from
documentation alone (spec §2 SCOPE-01, reference [1]).

Status: stub. Work package B03 (days 2-3).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from tandem.adapters.base import AdapterContext
from tandem.model.events import AgentFamily, Event
from tandem.sources.jsonl import Record
from tandem.versions import ADAPTER_VERSIONS


class ClaudeCodeAdapter:
    agent = AgentFamily.CLAUDE_CODE
    version = ADAPTER_VERSIONS["claude_code"]
    supported_versions: tuple[str, ...] = ()

    def parse(self, records: Iterable[Record], ctx: AdapterContext) -> Iterator[Event]:
        # TODO(B03): map user/assistant/tool_use/tool_result records; pair by tool_use id;
        # reconstruct Edit/Write/MultiEdit spans; read usage per message; keep cwd per record.
        for record in records:
            ctx.warn("adapter_not_implemented", "Claude Code adapter is a stub", record.offset)
        return iter(())
