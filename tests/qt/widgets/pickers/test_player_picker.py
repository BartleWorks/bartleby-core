"""Tests for the player menu every button that acts on one player shares."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QMenu, QWidget

from bartleby.qt.widgets.pickers.player_picker import pick_player, picker_label


@pytest.fixture
def menu_titles(monkeypatch):
    """Stand in for ``QMenu.exec``: record the menu's entries and trigger the one asked for."""
    shown: list[list[str]] = []
    choose: list[int] = []

    def _exec(menu, *_args):
        actions = menu.actions()
        shown.append([a.text() for a in actions])
        if choose:
            actions[choose.pop(0)].trigger()

    monkeypatch.setattr(QMenu, "exec", _exec)
    return shown, choose


def test_no_player_does_nothing(qapp, menu_titles):
    picked = []

    pick_player(QWidget(), 0, "Record", picked.append)

    assert picked == []
    assert menu_titles[0] == []


def test_one_player_is_picked_without_asking(qapp, menu_titles):
    picked = []

    pick_player(QWidget(), 1, "Record", picked.append)

    assert picked == [0]
    assert menu_titles[0] == []


def test_several_players_are_offered_by_number(qapp, menu_titles):
    shown, choose = menu_titles
    choose.append(2)
    picked = []

    pick_player(QWidget(), 3, "Record", picked.append)

    assert shown == [["Record from Player 1", "Record from Player 2", "Record from Player 3"]]
    assert picked == [2]


@pytest.mark.parametrize(("players", "label"), [(0, "Fill"), (1, "Fill"), (2, "Fill ▾"), (4, "Fill ▾")])
def test_the_label_is_marked_only_when_a_click_asks(players, label):
    assert picker_label("Fill", players) == label


def test_a_verb_that_goes_the_other_way_says_so(qapp, menu_titles):
    shown, _choose = menu_titles

    pick_player(QWidget(), 2, "Apply", lambda _index: None, preposition="to")

    assert shown == [["Apply to Player 1", "Apply to Player 2"]]
