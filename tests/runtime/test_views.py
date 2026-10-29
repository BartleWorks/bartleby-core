"""Views are frozen values, so a product can send one across threads and compare two."""

from __future__ import annotations

import dataclasses

import pytest

from bartleby.runtime.views import BADGE_NEUTRAL, CardBadge, PlayerRow, PlayerView


def test_a_view_is_frozen():
    view = PlayerView(hwnd=1, slot=1)

    with pytest.raises(dataclasses.FrozenInstanceError):
        view.zone = "Colossus Boulevard"


def test_two_views_of_the_same_values_are_equal():
    """BartleBuddy's session publishes only a view that changed, by this comparison."""

    def build() -> PlayerView:
        return PlayerView(hwnd=1, slot=1, rows=(PlayerRow("Holder", "idle"),), badges=(CardBadge("Leader"),))

    assert build() == build()
    assert build() != dataclasses.replace(build(), zone="Colossus Boulevard")


def test_a_view_starts_with_nothing_known():
    view = PlayerView(hwnd=1, slot=1)

    assert (view.color_key, view.wizard_name, view.zone) == (None, None, None)
    assert not view.unhooked and not view.loading
    assert CardBadge("Leader").kind == BADGE_NEUTRAL
