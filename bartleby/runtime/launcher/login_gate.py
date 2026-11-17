"""Which new windows the client manager must not hook yet, because a launch is logging them in.

The launcher logs a window in through wizwalker's ``memory_login``, which puts a stub on
the root window hook's site for as long as the login takes (``wizwalker/login.py``). The
two cannot share it: the one written second would replay the other's jump out of place,
and the game crashes. The client manager hooks every new window it sees, so it asks this
gate first, and leaves a held window for a later pass.

Two holds, because the launcher does not know which window is its own until it has
claimed one:

* **While a launch is spawning**, every new window is held. Between starting the game and
  claiming its window there is no handle to name, and the client manager polls on its own
  clock, so it can see the window first. A window the user opens meanwhile waits a few
  seconds with it.
* **Once claimed**, just the launch's own windows are held, until each one's login is
  sent or its launch gives up on it.

One gate for the process: the launcher and the client manager meet nowhere else, and a
product runs one of each.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager


class LoginGate:
    """The holds the launcher places and the client manager reads."""

    def __init__(self) -> None:
        self._spawning = 0
        self._logging_in: set[int] = set()

    @contextmanager
    def spawning(self) -> Iterator[None]:
        """Hold every new window while the body starts game clients and claims their windows."""
        self._spawning += 1
        try:
            yield
        finally:
            self._spawning -= 1

    def hold(self, handles: Iterable[int]) -> None:
        """Hold these windows until each is released."""
        self._logging_in.update(handles)

    def release(self, handles: Iterable[int]) -> None:
        """Let these windows be hooked: their logins are sent, or will not be."""
        self._logging_in.difference_update(handles)

    def holds(self, handle: int) -> bool:
        """Whether the window *handle* must not be hooked yet."""
        return self._spawning > 0 or handle in self._logging_in


gate = LoginGate()
