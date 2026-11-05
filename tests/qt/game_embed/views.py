"""The game view and its launch screen as a sample product builds them, for core's tests.

From the settings the screen fit was handed (``conftest.sample_settings``). And a refit pool
the test drives (``HeldPool``).
"""

from __future__ import annotations

from concurrent.futures import Future

from bartleby.qt import screen_fit
from bartleby.qt.game_embed.game_view import GameView, _GamePlaceholder


def make_game_view(parent=None, **kwargs) -> GameView:
    return GameView(screen_fit.settings(), parent, **kwargs)


def make_placeholder(parent=None, **kwargs) -> _GamePlaceholder:
    return _GamePlaceholder(screen_fit.settings(), parent, **kwargs)


class HeldPool:
    """A stand-in for ``client_tile._refit_pool`` that runs a refit's work when the test says.

    *held* False runs each piece of work as it is submitted, on the calling thread, so a refit
    is done by the time ``refit`` returns. *held* True keeps it until ``run``, which is a refit
    mid-flight: sent to the worker, and not back.
    """

    def __init__(self, *, held: bool = False) -> None:
        self.held = held
        self.waiting: list[tuple] = []

    def submit(self, fn, *args) -> Future:
        future: Future = Future()
        self.waiting.append((fn, args, future))
        if not self.held:
            # Everything, including what this submits as it runs, as a pool of workers would.
            while self.waiting:
                self.run()
        return future

    def run(self, count: int | None = None) -> int:
        """Run the oldest *count* pieces held, or all held when this is called, and say how many ran.

        Not what they submit as they run: that is the next refit, which waits for the next call.
        """
        batch = self.waiting[: len(self.waiting) if count is None else count]
        del self.waiting[: len(batch)]
        for fn, args, future in batch:
            future.set_result(fn(*args))
        return len(batch)
