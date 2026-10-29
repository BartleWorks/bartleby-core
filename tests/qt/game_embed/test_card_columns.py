"""``CardColumn`` on its own, and the two banner sentences it is handed.

The column moved to core with the player cards (Stage 8 of BartleBot #167), since the
Players tab is one. Its drag, its scrolling and its fit are held in BartleBot by the
launch cards' tests until those cards move too (Stage 9); here it holds plain widgets.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel

from bartleby.qt.game_embed.card_columns import CardColumn, account_banner_text, profile_banner_text
from bartleby.runtime.game.game_sizing import FitProblem


def _problem(available=(900, 600)) -> FitProblem:
    return FitProblem(resolution=(1280, 720), client_count=1, need=(1300, 760), available=available)


def test_the_account_banner_names_the_default_resolution_and_two_ways_out():
    text = account_banner_text(_problem())

    assert "1280×720" in text and "900×600" in text
    assert "fewer clients" not in text, "one account is one client"
    assert account_banner_text(None) == ""


def test_the_profile_banner_says_whether_some_or_all_profiles_do_not_fit():
    some = profile_banner_text([_problem()], total=3)
    every = profile_banner_text([_problem(), _problem()], total=2)

    assert some.startswith("Some launch profiles")
    assert "launch fewer clients" in every
    assert profile_banner_text([], total=2) == ""


def test_an_untitled_column_with_no_add_card_holds_only_its_cards(qapp, parentless_shows):
    column = CardColumn("", "Nothing here yet", add_text="")
    cards = [QLabel("one"), QLabel("two")]

    column.set_cards(cards)

    assert column.cards == cards
    assert column.add_card is None
    assert parentless_shows == []


def test_the_empty_hint_shows_only_while_there_are_no_cards(qapp):
    column = CardColumn("", "Nothing here yet")
    hint = lambda: [w for w in column.findChildren(QLabel) if w.text() == "Nothing here yet"]  # noqa: E731

    column.set_cards([])
    assert hint()

    column.set_cards([QLabel("one")])
    qapp.sendPostedEvents(None, 52)  # DeferredDelete: the old rows go
    assert not hint()


def test_an_add_card_ends_the_column_and_is_not_one_of_its_cards(qapp):
    column = CardColumn("Things", add_text="Add a thing")
    added: list[bool] = []
    column.add_requested.connect(lambda: added.append(True))

    column.set_cards([QLabel("one")])

    assert column.add_card is not None
    assert column.cards == [column.cards[0]] and column.add_card not in column.cards
    column.add_card.add_requested.emit()
    assert added == [True]
