"""Adapter contract.

An adapter turns one provider's raw records into normalized ``Event`` objects.
Provider-specific parsing stays here; downstream stages see only the envelope.
Adapters must:

- pair tool calls with results by provider ID and source order;
- never mark an edit succeeded without a successful result;
- keep unknown fields as diagnostics instead of failing the scan;
- report the provider versions and fixture families they support.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Protocol

from tandem.model.events import AgentFamily, Event
from tandem.sources.jsonl import Record


@dataclass(slots=True)
class AdapterContext:
    source_id: str
    source_generation: int
    source_path: str
    diagnostics: list[tuple[str, str, int | None]] = field(default_factory=list)
    """(code, message, record_offset) collected while parsing."""

    def warn(self, code: str, message: str, offset: int | None = None) -> None:
        self.diagnostics.append((code, message, offset))


class Adapter(Protocol):
    agent: AgentFamily
    version: str
    supported_versions: tuple[str, ...]
    """Provider CLI versions validated against pinned fixtures."""

    def parse(self, records: Iterable[Record], ctx: AdapterContext) -> Iterator[Event]: ...
