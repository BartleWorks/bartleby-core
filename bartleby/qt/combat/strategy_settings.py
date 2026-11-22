"""What the Combat Strategies window asks of a product's settings.

A settings protocol, as the launcher's ``LaunchSettings`` is: each product's settings object
answers it, and is handed to the window (``CombatStrategyDialog(ws, settings)``). It is the
favorites, and nothing else. They are kept per app: which strategies one user reaches for in
BartleBot says nothing about which they reach for in BartleBuddy, and neither app writes the
other's settings file.
"""

from __future__ import annotations

from typing import Protocol


class StrategySettings(Protocol):
    """A product's settings, as the window reads and writes them.

    *kind* is a ``StrategyKind``'s value: ``"duel"``, ``"player"`` or ``"deck"``.
    """

    def favorite_strategies(self, kind: str) -> list[str]:
        """The favorited ids of one kind. An id whose strategy is gone may stay in it, and matches nothing."""
        ...

    def is_favorite_strategy(self, kind: str, strategy_id: str) -> bool: ...

    def toggle_favorite_strategy(self, kind: str, strategy_id: str) -> bool:
        """Toggle one strategy in its kind's favorites and save at once. Returns True if now favorited."""
        ...
