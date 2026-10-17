from __future__ import annotations

from pathlib import Path

from tandem.adapters.base import Adapter, AdapterContext
from tandem.model.events import Event, EventType
from tandem.sources.jsonl import read_incremental

FIXTURES = Path(__file__).parent / "fixtures"


def parse_fixture(adapter: Adapter, rel: str) -> tuple[list[Event], AdapterContext]:
    path = FIXTURES / rel
    result = read_incremental(path)
    assert not result.quarantined
    ctx = AdapterContext(source_id="src-1", source_generation=0, source_path=str(path))
    return list(adapter.parse(result.records, ctx)), ctx


def of_type(events: list[Event], kind: EventType) -> list[Event]:
    return [e for e in events if e.action.event_type is kind]
