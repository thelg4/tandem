"""Exit codes and error types."""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    """Command completed, including a clearly marked partial report."""
    NOT_IMPLEMENTED = 1
    """Temporary: the command exists in the contract but not yet in this build."""
    INVALID_INPUT = 2
    """Invalid input or configuration."""
    UNAVAILABLE = 3
    """Repository or source data unavailable; no usable analysis."""
    PROCESSING_FAILURE = 4
    """Processing or database failure."""


class TandemError(Exception):
    exit_code: ExitCode = ExitCode.PROCESSING_FAILURE


class InvalidInputError(TandemError):
    exit_code = ExitCode.INVALID_INPUT


class UnavailableError(TandemError):
    exit_code = ExitCode.UNAVAILABLE


class GitError(TandemError):
    exit_code = ExitCode.UNAVAILABLE


class StorageError(TandemError):
    exit_code = ExitCode.PROCESSING_FAILURE


class SchemaCompatibilityError(StorageError):
    """Database was written by a newer, unsupported schema."""


class ScanBusyError(TandemError):
    """Another scan holds the single writer lock."""

    exit_code = ExitCode.PROCESSING_FAILURE
