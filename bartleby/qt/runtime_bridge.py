"""The runtime on its own thread, and the one crossing between it and Qt.

Moved from BartleBuddy's ``bartlebuddy/runtime/bridge.py`` (Stage 5 of BartleBot #167).
An app that steers clients on a tick runs its runtime on a plain asyncio loop in a worker
thread, and Qt keeps the main thread. BartleBuddy does: following steers every 50 ms on up
to four clients, so on a loop shared with the GUI any GUI stall would jitter every
follower at once. BartleBot runs everything on one qasync loop instead, since nothing of
its steers on a tick (``docs/ARCHITECTURE.md``, "Where the runtime runs").

The cost is discipline at the crossing, and this module is all of it. Only snapshots cross
out (a product's views, log records), as queued Qt signals the GUI thread receives: a
signal emitted on the runtime thread reaches a slot on the GUI thread through Qt's queue,
because the :class:`RuntimeSignals` object lives on the GUI thread. Only commands cross in,
as coroutine functions :meth:`RuntimeThread.submit` schedules on the loop. The GUI never
touches a wizwalker object. A client manager on the runtime thread hands its tiles to a
game view on the GUI thread through a :class:`TileCrossing`, the same way.

The jitter is real, and measured (BartleBuddy's ``tools/probe_follow_jitter.py``, Stage 4
of BartleBot #167): on one shared loop each tile resize held every follower for 1.27 s,
while on this thread no follower went more than 66 ms without steering through the same
resizes.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from concurrent.futures import Future

from PyQt6.QtCore import QObject, Qt, pyqtSignal

logger = logging.getLogger(__name__)


class RuntimeSignals(QObject):
    """Signals the runtime thread emits; Qt queues each to the GUI thread.

    Made on the GUI thread. A product adds the signals for its own views by subclassing.
    """

    #: A record logged on the runtime thread, for the Log tab.
    log_record = pyqtSignal(object)


class TileCrossing(QObject):
    """A client manager's ``TileView`` on the runtime thread, drawing into a game view on the GUI thread.

    The manager calls ``sync_tiles`` on every detection tick, and ``unembed_all`` as it
    shuts down; each is sent on as a queued signal to *view*, a core ``GameView``. So is
    each wizard name list, which the product connects from the manager's
    ``wizard_names_changed``. Queued even when sent from the GUI thread, so the view only
    ever hears of a change from Qt's queue, in the order it was sent. Never blocking: a
    shutdown that waits on the runtime from the GUI thread would deadlock on it.

    Made on the GUI thread, with the view.
    """

    _tiles = pyqtSignal(object)
    _wizard_names = pyqtSignal(object)
    _unembed = pyqtSignal()

    def __init__(self, view) -> None:
        super().__init__()
        queued = Qt.ConnectionType.QueuedConnection
        self._tiles.connect(view.sync_tiles, queued)
        self._wizard_names.connect(view.set_wizard_names, queued)
        self._unembed.connect(view.unembed_all, queued)

    def sync_tiles(self, wanted) -> None:
        self._tiles.emit(list(wanted))

    def set_wizard_names(self, names) -> None:
        self._wizard_names.emit(list(names))

    def unembed_all(self) -> None:
        self._unembed.emit()


class RuntimeThread:
    """An asyncio loop on a thread of its own, named *name* (the product's: ``bartlebuddy-runtime``)."""

    def __init__(self, name: str = "runtime") -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._started = threading.Event()

    @property
    def name(self) -> str:
        return self._thread.name

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._started.set)
        self.loop.run_forever()
        # Let whatever is left finish being cancelled, then close.
        pending = asyncio.all_tasks(self.loop)
        for task in pending:
            task.cancel()
        if pending:
            self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.close()

    def start(self) -> None:
        self._thread.start()
        self._started.wait()

    def submit(self, work: Callable[[], Awaitable], *, what: str = "") -> Future:
        """Run ``work()`` on the runtime's loop. Safe from any thread; failures are logged."""

        async def guarded():
            try:
                return await work()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s failed", what or getattr(work, "__name__", "A command"))

        return asyncio.run_coroutine_threadsafe(guarded(), self.loop)

    def call(self, fn: Callable[[], object]) -> None:
        """Run a plain function on the loop's thread."""
        self.loop.call_soon_threadsafe(fn)

    def stop(self, *, timeout: float = 10.0) -> None:
        if self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout)
