"""Core's half of the screen fit: what it reads of a product, and the pieces that read nothing.

The product's half -- its panes and the layout it fits them to -- is the product's, and
BartleBot's ``tests/gui/test_screen_fit.py`` and ``test_window_panes.py`` drive this module
through BartleBot's settings. Here, the sample settings ``conftest.sample_settings`` hands it.
"""

from __future__ import annotations

import pytest

from bartleby.qt import screen_fit
from bartleby.runtime.game import game_sizing
from bartleby.runtime.game.game_sizing import BOTTOM, LEFT, Pane

_1080P = ((1920, 1080), 1.0)


@pytest.fixture
def at_1080p(monkeypatch):
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: _1080P)


def test_an_unconfigured_fit_says_so(monkeypatch):
    monkeypatch.setattr(screen_fit, "_settings", None)

    with pytest.raises(RuntimeError, match="configure"):
        screen_fit.settings()


def test_the_budget_is_the_screen_less_the_product_s_panes_at_their_sizes(sample_settings, at_1080p):
    sample_settings.panes = [Pane("left", 250, LEFT), Pane("below", 180, BOTTOM)]
    sample_settings.sizes = {"left": 400}

    assert screen_fit.budget_for() == ((1920 - 400 - 8, 1080 - 180 - 60), 1.0)


def test_the_panes_are_asked_for_again_at_each_budget(sample_settings, at_1080p):
    """A product hides a pane, or a floor follows the font: the list is never cached."""
    sample_settings.panes = []
    wide = screen_fit.budget_for()[0]
    sample_settings.panes = [Pane("sidebar", 300, "right")]

    assert screen_fit.budget_for()[0] == (wide[0] - 308, wide[1])


def test_the_live_area_widens_the_budget_but_never_narrows_it(sample_settings, at_1080p):
    configured = screen_fit.budget_for()[0]

    assert screen_fit.budget_for(live_area=(5000, 10))[0] == (5000, configured[1])


def test_a_profile_without_a_resolution_follows_the_product_s(sample_settings):
    sample_settings.game_resolution = (960, 540)

    assert screen_fit.profile_resolution({"accounts": ["a"]}) == (960, 540)
    assert screen_fit.profile_resolution({"accounts": ["a"], "resolution": [1280, 720]}) == (1280, 720)


def test_tiles_of_two_sizes_are_judged_as_the_layout_needs_them(at_1080p):
    main, side = game_sizing.TileSize.MAIN, game_sizing.TileSize.SIDE
    budget, _ = screen_fit.budget_for()
    width, height = game_sizing.required_game_area((1280, 720), [main, side, side])

    assert (width <= budget[0] and height <= budget[1]) == (
        screen_fit.fit_details((1280, 720), [main, side, side]) is None
    )


def test_an_unmeasurable_screen_refuses_nothing(monkeypatch):
    monkeypatch.setattr(screen_fit, "screen_metrics", lambda _widget=None: (screen_fit.UNMEASURABLE, 1.0))

    assert screen_fit.fit_details((3840, 2160), 4) is None


def test_a_window_is_cut_to_the_screen_but_never_below_its_minimum():
    assert screen_fit.size_within_screen((3000, 900), (800, 600), (1920, 1080)) == (1880, 900)
    assert screen_fit.size_within_screen((900, 900), (1000, 1000), (800, 800)) == (1000, 1000)
