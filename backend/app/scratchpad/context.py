"""Context-var holder for the active session FactStore.

The ``search_facts`` tool needs to read from "the vault the current PR
review is using". Rather than threading a FactStore parameter through
every tool dispatch path (there are many), we stash it in a
``contextvars.ContextVar`` which propagates naturally across asyncio
tasks AND across threads the executor creates.

PRBrainOrchestrator.run_stream sets the var at start, clears it at end.
Tool implementations read via ``current_factstore()`` — None when not
running inside a PR review, and the tool short-circuits with a friendly
message in that case.

This is the same pattern Claude Code uses for per-turn scoped state;
contextvars + copy_context() are the Python-stdlib equivalent of
async-local storage.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, Optional

if TYPE_CHECKING:
    from .store import FactStore


_current_store: contextvars.ContextVar[Optional[FactStore]] = contextvars.ContextVar(
    "conductor_scratchpad_store", default=None
)

# Phase 9.9.3: track which sub-agent is currently executing so
# `update_notes` can key notes by (agent, topic) without the agent
# having to restate its name on every call. Set by AgentLoopService
# at run() start, cleared at end.
_current_agent_name: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "conductor_current_agent_name", default=None
)


def current_factstore() -> Optional[FactStore]:
    """Return the FactStore bound to the current task, or None.

    Safe to call anywhere in the backend — returns None when no PR review
    is active, so tool code can branch on availability without raising.
    """
    return _current_store.get()


def current_agent_name() -> Optional[str]:
    """Return the name of the sub-agent currently executing (Phase 9.9.3).

    Used by `update_notes` to key notes by (agent, topic) without the
    agent explicitly passing its name. Returns None when called outside
    an agent run.
    """
    return _current_agent_name.get()


@contextmanager
def bind_factstore(store: FactStore) -> Iterator[None]:
    """Bind ``store`` as the current session for the lifetime of the
    context manager. On exit, restore the previous binding (usually
    ``None``)."""
    token = _current_store.set(store)
    try:
        yield
    finally:
        _current_store.reset(token)


@contextmanager
def bind_agent_name(name: str) -> Iterator[None]:
    """Bind ``name`` as the current agent for the lifetime of the
    context manager. On exit, restore the previous binding."""
    token = _current_agent_name.set(name)
    try:
        yield
    finally:
        _current_agent_name.reset(token)
