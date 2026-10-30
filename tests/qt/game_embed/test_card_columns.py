"""``CardColumn`` on its own, and the two banner sentences it is handed.

The column moved to core with the player cards (Stage 8 of BartleBot #167), since the
Players tab is one. Its drag, its scrolling and its fit are held in BartleBot by the
launch cards' tests until those cards move too (Stage 9); here it holds plain widgets.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from bartleby.qt.game_embed.card_columns import CardColumn, account_banner_text, profile_banner_text
from bartleby.qt.game_embed.launch_cards import AccountCard, ProfileCard
from bartleby.qt.offscreen_warmup import WARMED_PROPERTY
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


# -- Where a titled column's heading, rule and cards sit (BartleBot's, until Stage 9 of #167) --

PROFILE = {"name": "Farm Team", "accounts": ["someone@example.com"]}


def _shown_column(qapp, title: str) -> CardColumn:
    column = CardColumn(title, "hint", "Add launch profile")
    column.set_cards([ProfileCard(dict(PROFILE), [])])
    column.resize(400, 400)
    column.show()
    qapp.processEvents()
    return column


def _span(widget, column: CardColumn) -> tuple[int, int]:
    """*widget*'s left and right edges, in *column*'s coordinates."""
    left = widget.mapTo(column, QPoint(0, 0)).x()
    return left, left + widget.width()


def test_a_cards_frame_spans_exactly_the_rule(qapp):
    column = _shown_column(qapp, "Launch Profiles")
    (rule,) = [f for f in column.findChildren(QFrame) if f.frameShape() == QFrame.Shape.HLine]

    assert _span(column.cards[0].frame, column) == _span(rule, column)
    column.close()


def test_the_grip_sits_outside_the_rule_and_the_rule_is_centered(qapp):
    column = _shown_column(qapp, "Launch Profiles")
    (rule,) = [f for f in column.findChildren(QFrame) if f.frameShape() == QFrame.Shape.HLine]
    rule_left, rule_right = _span(rule, column)

    assert _span(column.cards[0].grip, column)[1] <= rule_left
    assert rule_left == column.width() - rule_right
    column.close()


def test_the_add_card_lines_up_with_the_cards_above_it(qapp):
    column = _shown_column(qapp, "Launch Profiles")

    assert _span(column.add_card, column)[1] == _span(column.cards[0].frame, column)[1]
    column.close()


def test_a_scroll_bar_takes_its_width_from_the_gutter_not_the_cards(qapp):
    """It narrows the viewport, and taking the whole gutter out of what was left put the
    cards a bar's width short of the rule, off center under it."""
    column = CardColumn("Launch Profiles", "hint", "Add launch profile")
    column.set_cards([ProfileCard({"name": f"Profile {i}", "accounts": ["someone@example.com"]}, []) for i in range(8)])
    column.resize(400, 250)
    column.show()
    qapp.processEvents()
    (rule,) = [f for f in column.findChildren(QFrame) if f.frameShape() == QFrame.Shape.HLine]
    bar = column.scroll_area.verticalScrollBar()

    assert bar.isVisible(), "the column has to overflow for this to test anything"
    assert _span(column.cards[0].frame, column) == _span(rule, column)
    assert _span(bar, column)[0] >= _span(rule, column)[1], "the bar sits in the right gutter"
    column.close()


def test_a_scroll_bar_that_has_gone_gives_its_width_back(qapp):
    """Qt hides the bar's container, not the bar, so a column that had overflowed once
    went on giving up the bar's width after the bar was gone -- in the Game tab, the
    column without a bar ran past its rule beside the one with a bar."""
    column = CardColumn("Launch Profiles", "hint", "Add launch profile")
    column.set_cards([ProfileCard({"name": f"Profile {i}", "accounts": ["someone@example.com"]}, []) for i in range(8)])
    column.resize(400, 250)
    column.show()
    qapp.processEvents()
    bar = column.scroll_area.verticalScrollBar()
    assert bar.isVisible(), "the column has to overflow first for this to test anything"

    column.resize(400, 2000)
    qapp.processEvents()
    (rule,) = [f for f in column.findChildren(QFrame) if f.frameShape() == QFrame.Shape.HLine]

    assert not bar.isVisibleTo(column.scroll_area)
    assert _span(column.cards[0].frame, column) == _span(rule, column)
    column.close()


def test_an_untitled_column_keeps_its_full_width(qapp):
    """No rule to line up with, so no right gutter narrowing the cards: the Players tab's
    column is in a sidebar."""
    column = _shown_column(qapp, "")

    assert _span(column.cards[0].frame, column)[1] == column.scroll_area.viewport().width()
    column.close()


# ---------------------------------------------------------------------------
# The first offscreen render is paid on a show (bartleby.qt.offscreen_warmup)
# ---------------------------------------------------------------------------

_WARMUP_ACCOUNTS = [
    {"username": "one@example.com", "alias": "One"},
    {"username": "two@example.com", "alias": "Two"},
]


def test_showing_a_card_column_warms_its_window(qapp):
    column = CardColumn("Accounts", "hint", "Add account")
    column.set_cards([AccountCard(a, [], _WARMUP_ACCOUNTS) for a in _WARMUP_ACCOUNTS])
    host = QWidget()
    QVBoxLayout(host).addWidget(column)

    host.show()
    for _ in range(3):
        qapp.processEvents()

    assert host.property(WARMED_PROPERTY) is True, (
        "a drag would otherwise pay Qt's first offscreen render between two mouse moves"
    )
