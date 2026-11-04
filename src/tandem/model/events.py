"""Normalized, versioned event envelope.

Every adapter emits these records. Provider-specific parsing stays inside the
adapter; attribution consumes only normalized records. Missing data is ``None``
plus a reason, never an invented timestamp, token total, or success flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from tandem.model.edits import ObservedEdit
from tandem.versions import EVENT_ENVELOPE_VERSION


class AgentFamily(StrEnum):
    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    HUMAN = "human"
    """Only with explicit evidence or a stored user annotation."""
    UNKNOWN = "unknown"


class EventType(StrEnum):
    PROMPT = "prompt"
    RESPONSE = "response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    TEXT_EDIT = "text_edit"
    SHELL_COMMAND = "shell_command"
    USAGE = "usage"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    COMMIT_OBSERVATION = "commit_observation"


class ActionStatus(StrEnum):
    """A call without a successful result is never a completed edit."""

    ATTEMPTED = "attempted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class TimeQuality(StrEnum):
    EXACT = "exact"
    COARSE = "coarse"
    INFERRED_FROM_NEIGHBOR = "inferred_from_neighbor"
    MISSING = "missing"


class PathResolution(StrEnum):
    RESOLVED = "resolved"
    OUTSIDE_REPO = "outside_repo"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class UsageBasis(StrEnum):
    PER_CALL = "per_call"
    CUMULATIVE = "cumulative"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Missing:
    """Explicit absence with a reason code. Never rendered as zero."""

    reason: str


@dataclass(frozen=True, slots=True)
class Identity:
    event_id: str
    source_id: str
    source_generation: int
    record_offset: int
    adapter_version: str
    provider_event_id: str | None = None
    schema_version: str = EVENT_ENVELOPE_VERSION


@dataclass(frozen=True, slots=True)
class Actor:
    agent_family: AgentFamily
    provider_session_id: str
    session_id: str
    parent_session_id: str | None = None
    model_id: str | None = None
    """Nullable and may vary by turn."""


@dataclass(frozen=True, slots=True)
class Scope:
    recorded_cwd: str | None
    effective_cwd: str | None
    relative_path: str | None
    path_resolution: PathResolution
    repo_id: str | None = None
    worktree_id: str | None = None


@dataclass(frozen=True, slots=True)
class Order:
    sequence: int
    """Per-source sequence; preserves causal order within one transcript."""
    raw_timestamp: str | None
    utc_timestamp: str | None
    time_quality: TimeQuality
    observed_at: str | None = None


@dataclass(frozen=True, slots=True)
class Action:
    event_type: EventType
    status: ActionStatus
    tool_name: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class Content:
    before_blob: str | None = None
    after_blob: str | None = None
    patch_ref: str | None = None
    before_digest: str | None = None
    after_digest: str | None = None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Usage:
    basis: UsageBasis
    categories: dict[str, int] = field(default_factory=dict)
    """Raw provider categories, e.g. input, output, cache_read, cache_write."""
    complete: bool = False


@dataclass(frozen=True, slots=True)
class SourceLocator:
    path: str
    byte_offset: int
    byte_length: int


@dataclass(frozen=True, slots=True)
class Evidence:
    locator: SourceLocator
    reason_codes: tuple[str, ...] = ()
    git_refs: tuple[str, ...] = ()
    analyzer_version: str | None = None


@dataclass(frozen=True, slots=True)
class Event:
    identity: Identity
    actor: Actor
    scope: Scope
    order: Order
    action: Action
    evidence: Evidence
    content: Content | None = None
    usage: Usage | None = None
    edit: ObservedEdit | None = None
    """Set on TEXT_EDIT events: one file changed by one successful edit."""
