"""Codex rollout adapter.

Reads rollout JSONL files, commonly under ~/.codex/sessions. Record the upstream
recorder commit and installed CLI version when implementing (spec reference [3]).

Status: stub. Work package B03 (days 2-3).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from tandem.adapters.base import AdapterContext
from tandem.model.events import AgentFamily, Event
from tandem.sources.jsonl import Record
from tandem.versions import ADAPTER_VERSIONS


class CodexAdapter:
    agent = AgentFamily.CODEX
    version = ADAPTER_VERSIONS["codex"]
    supported_versions: tuple[str, ...] = ()

    def parse(self, records: Iterable[Record], ctx: AdapterContext) -> Iterator[Event]:
        # TODO(B03): map session_meta / response_item / event_msg records; pair function calls
        # with outputs by call_id; parse apply_patch edits; treat token counts as cumulative.
        for record in records:
            ctx.warn("adapter_not_implemented", "Codex adapter is a stub", record.offset)
        return iter(())
