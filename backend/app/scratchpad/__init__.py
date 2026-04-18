"""Scratchpad — task-scoped short-term memory for PR Brain sub-agents.

Phase 9.15 MVP: in-flight dedup for expensive cache misses. Full SQLite-backed
fact vault is a follow-up in the same sprint. See docs/SHORT_TERM_MEMORY_DESIGN.md.

Public surface (MVP):
    key_lock(key) → threading.Lock
        Get-or-create a per-key lock. Different keys don't serialise against each
        other; same-key callers block the second-onwards so only one does the work.
"""

from .inflight import key_lock

__all__ = ["key_lock"]
