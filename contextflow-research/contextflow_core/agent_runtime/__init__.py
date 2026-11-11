"""Formal Agent Runtime adapter layer (C-03).

Public surface for the future Core Dispatcher:

* contracts: capabilities, request, handle, snapshot, result, cancel result
* adapters: Codex CLI, Claude Code CLI, DSH, Codex-Relay (+ desktop descriptor)
* registry: runtime selection and capability matrix
* journal: local idempotency safety net (Core Delivery Store stays authoritative)

This package does not create Tasks, Deliveries or Dispatcher state.
"""

from .base import AgentRuntimeAdapter, ConcurrencyGuard, ProcessRuntimeAdapter
from .config import RuntimeConfig, load_config
from .contracts import (
    AgentCapabilities,
    CancelDisposition,
    CancelResult,
    ControlKind,
    EvidenceStatus,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionResult,
    ExecutionSnapshot,
    ExecutionState,
    FailureKind,
    SessionPolicy,
    TERMINAL_STATES,
    is_terminal,
    utc_now,
)
from .errors import (
    AdapterError,
    ConcurrencyLimit,
    ExecutionNotFound,
    IdempotencyConflict,
    InvalidRequest,
    ProtocolError,
    RuntimeUnavailable,
    SessionUnavailable,
    UnsupportedOperation,
)
from .journal import ExecutionJournal, JournalRecord
from .registry import RUNTIME_KINDS, RuntimeRegistry, create_registry

__all__ = [
    "AdapterError",
    "AgentCapabilities",
    "AgentRuntimeAdapter",
    "CancelDisposition",
    "CancelResult",
    "ConcurrencyGuard",
    "ConcurrencyLimit",
    "ControlKind",
    "EvidenceStatus",
    "ExecutionHandle",
    "ExecutionJournal",
    "ExecutionNotFound",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionSnapshot",
    "ExecutionState",
    "FailureKind",
    "IdempotencyConflict",
    "InvalidRequest",
    "JournalRecord",
    "ProcessRuntimeAdapter",
    "ProtocolError",
    "RUNTIME_KINDS",
    "RuntimeConfig",
    "RuntimeRegistry",
    "RuntimeUnavailable",
    "SessionPolicy",
    "SessionUnavailable",
    "TERMINAL_STATES",
    "UnsupportedOperation",
    "create_registry",
    "is_terminal",
    "load_config",
    "utc_now",
]
