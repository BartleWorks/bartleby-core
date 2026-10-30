"""The game view and its launch screen as a sample product builds them, for core's tests.

From the settings the screen fit was handed (``conftest.sample_settings``).
"""

from __future__ import annotations

from bartleby.qt import screen_fit
from bartleby.qt.game_embed.game_view import GameView, _GamePlaceholder


def make_game_view(parent=None, **kwargs) -> GameView:
    return GameView(screen_fit.settings(), parent, **kwargs)


def make_placeholder(parent=None, **kwargs) -> _GamePlaceholder:
    return _GamePlaceholder(screen_fit.settings(), parent, **kwargs)
