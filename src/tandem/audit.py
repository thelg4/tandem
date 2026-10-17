"""``tandem audit``: a structure-only summary of local agent logs (spec §2 SCOPE-01/02).

Reports CLI versions, record types, tool names, and what the adapters recover:
edits by evidence source, failed edit calls, reconstruction checks, and diagnostics.
It never prints prompts, code, file contents, or file paths, so the output is safe
to paste into an issue or share when checking adapter support.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from tandem.adapters import AdapterContext, ClaudeCodeAdapter, CodexAdapter
from tandem.adapters.base import Adapter
from tandem.diffs import ApplyError, apply_hunks
from tandem.model.events import ActionStatus, AgentFamily, EventType
from tandem.sources.discovery import SourceRoot, iter_source_files
from tandem.sources.jsonl import read_incremental

EDIT_TOOL_NAMES = frozenset({"Write", "Edit", "MultiEdit", "apply_patch"})


@dataclass(slots=True)
class AgentAudit:
    agent: str
    root_found: bool
    files: int = 0
    bytes: int = 0
    records: int = 0
    malformed_records: int = 0
    versions: Counter[str] = field(default_factory=Counter)
    record_types: Counter[str] = field(default_factory=Counter)
    tools: Counter[str] = field(default_factory=Counter)
    events: Counter[str] = field(default_factory=Counter)
    edits: Counter[str] = field(default_factory=Counter)
    failed_edit_calls: int = 0
    reconstruction: Counter[str] = field(default_factory=Counter)
    diagnostics: Counter[str] = field(default_factory=Counter)

    def to_json(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "root_found": self.root_found,
            "files": self.files,
            "bytes": self.bytes,
            "records": self.records,
            "malformed_records": self.malformed_records,
            "versions": dict(self.versions.most_common()),
            "record_types": dict(self.record_types.most_common()),
            "tools": dict(self.tools.most_common()),
            "events": dict(self.events.most_common()),
            "edits": dict(self.edits.most_common()),
            "failed_edit_calls": self.failed_edit_calls,
            "reconstruction": dict(self.reconstruction),
            "diagnostics": dict(self.diagnostics.most_common()),
        }


def _adapter(agent: AgentFamily) -> Adapter:
    return ClaudeCodeAdapter() if agent is AgentFamily.CLAUDE_CODE else CodexAdapter()


def _structure(agent: AgentFamily, rec: dict[str, Any], out: AgentAudit) -> None:
    kind = rec.get("type")
    payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else None
    label = str(kind)
    if payload is not None and isinstance(payload.get("type"), str):
        label = f"{kind}/{payload['type']}"
    out.record_types[label] += 1
    if agent is AgentFamily.CLAUDE_CODE:
        if isinstance(rec.get("version"), str):
            out.versions[rec["version"]] += 1
        message = rec.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                out.tools[str(block.get("name"))] += 1
    elif payload is not None:
        if kind == "session_meta" and isinstance(payload.get("cli_version"), str):
            out.versions[payload["cli_version"]] += 1
        if payload.get("type") in ("function_call", "custom_tool_call", "local_shell_call"):
            out.tools[str(payload.get("name") or payload.get("type"))] += 1


def audit_root(root: SourceRoot, *, limit: int | None = None) -> AgentAudit:
    out = AgentAudit(agent=root.agent.value, root_found=root.available)
    adapter = _adapter(root.agent)
    for n, source in enumerate(iter_source_files(root)):
        if limit is not None and n >= limit:
            break
        result = read_incremental(source.path)
        out.files += 1
        out.bytes += source.size
        out.records += len(result.records)
        out.malformed_records += len(result.quarantined)
        for record in result.records:
            _structure(root.agent, record.value, out)
        ctx = AdapterContext(source_id=f"audit-{n}", source_generation=0, source_path="<redacted>")
        for event in adapter.parse(result.records, ctx):
            out.events[event.action.event_type.value] += 1
            if (
                event.action.event_type is EventType.TOOL_RESULT
                and event.action.tool_name in EDIT_TOOL_NAMES
                and event.action.status is ActionStatus.FAILED
            ):
                out.failed_edit_calls += 1
            edit = event.edit
            if edit is None:
                continue
            out.edits[f"{edit.source.value}:{edit.change.value}:{edit.call_link.value}"] += 1
            if edit.before_text is not None and edit.hunks:
                try:
                    apply_hunks(edit.before_text, edit.hunks)
                    out.reconstruction["exact"] += 1
                except ApplyError:
                    out.reconstruction["mismatch"] += 1
        out.diagnostics.update(code for code, _, _ in ctx.diagnostics)
    return out


def render_text(audits: list[AgentAudit]) -> str:
    lines = ["Tandem log audit (structure only: no prompts, code, or paths)", ""]
    for a in audits:
        lines.append(f"== {a.agent}")
        if not a.root_found:
            lines += ["   log folder not found", ""]
            continue
        mb = a.bytes / 1_000_000
        lines.append(
            f"   files {a.files}, {mb:.1f} MB, records {a.records}, malformed {a.malformed_records}"
        )
        for title, counter in (
            ("versions", a.versions),
            ("edits (source:change:link)", a.edits),
            ("events", a.events),
            ("tools", a.tools),
            ("record types", a.record_types),
            ("diagnostics", a.diagnostics),
        ):
            if counter:
                lines.append(f"   {title}:")
                lines += [f"     {k}: {v}" for k, v in counter.most_common(25)]
        lines.append(f"   failed edit calls: {a.failed_edit_calls}")
        if a.reconstruction:
            lines.append(f"   reconstruction from log: {dict(a.reconstruction)}")
        lines.append("")
    return "\n".join(lines)


def render_json(audits: list[AgentAudit]) -> str:
    return json.dumps({"audit": [a.to_json() for a in audits]}, indent=2, sort_keys=True)
