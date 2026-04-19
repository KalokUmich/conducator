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
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .store import FactStore


_current_store: contextvars.ContextVar[Optional[FactStore]] = contextvars.ContextVar(
    "conductor_scratchpad_store", default=None
)


def current_factstore() -> Optional[FactStore]:
    """Return the FactStore bound to the current task, or None.

    Safe to call anywhere in the backend — returns None when no PR review
    is active, so tool code can branch on availability without raising.
    """
    return _current_store.get()


@contextmanager
def bind_factstore(store: FactStore):
    """Bind ``store`` as the current session for the lifetime of the
    context manager. On exit, restore the previous binding (usually
    ``None``)."""
    token = _current_store.set(store)
    try:
        yield
    finally:
        _current_store.reset(token)
