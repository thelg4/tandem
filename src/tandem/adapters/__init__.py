"""Provider adapters: discover, parse, pair tool results, normalize usage."""

from tandem.adapters.base import Adapter, AdapterContext
from tandem.adapters.claude_code import ClaudeCodeAdapter
from tandem.adapters.codex import CodexAdapter

__all__ = ["Adapter", "AdapterContext", "ClaudeCodeAdapter", "CodexAdapter"]
