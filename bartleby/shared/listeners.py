"""A Qt-free stand-in for a signal: listeners connected, then called on each emit.

Shaped like a bound ``pyqtSignal`` (``connect``, ``disconnect``, ``emit``) on purpose. A
Qt-free class publishes through these, and a product with a Qt GUI can hand its own
signals in their place, so its connections cross threads the way Qt's do and the class's
code reads the same either way. BartleBot's client manager does exactly that.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)


class Listeners:
    """The callables to call, in the order they connected, each time something is emitted.

    One listener that raises is logged and the rest still hear it, as a Qt signal's slots
    would: a listener's bug is its own, never the emitter's.
    """

    def __init__(self, name: str = "listeners") -> None:
        self._name = name
        self._listeners: list[Callable[..., object]] = []

    def connect(self, listener: Callable[..., object]) -> Callable[[], None]:
        """Call *listener* on every emit from now on. Returns what disconnects it."""
        self._listeners.append(listener)
        return lambda: self.disconnect(listener)

    def disconnect(self, listener: Callable[..., object]) -> None:
        """Stop calling *listener*. Quietly does nothing for one never connected."""
        if listener in self._listeners:
            self._listeners.remove(listener)

    def emit(self, *args: object) -> None:
        for listener in list(self._listeners):
            try:
                listener(*args)
            except Exception:
                logger.exception("A %s listener raised", self._name)

    def __len__(self) -> int:
        return len(self._listeners)
