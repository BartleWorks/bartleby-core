"""``AddCard``: the dashed card that ends a column of cards and adds one more.

BartleBot's Quick Launch columns and its Combat tab end with one. How it sits in those
columns, and the editors it opens there, are tested in BartleBot's own
``tests/gui/widgets/cards/test_add_card.py``; what is here is the card itself.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QEnterEvent, QMouseEvent

import bartleby.qt.theming.theme as theme_mod
from bartleby.qt.widgets.cards.add_card import ADD_CARD_BORDER_WIDTH, AddCard
from bartleby.qt.widgets.cards.card_frame import GRIP_GUTTER


@pytest.fixture
def theme():
    before = theme_mod.active_theme()
    yield theme_mod.set_theme
    theme_mod.set_theme_instance(before)


def test_a_right_click_does_not_open_the_editor(qapp):
    """The card is a hit target for the button that means "do it", as the launch cards are."""
    card = AddCard("Add account")
    fired = []
    card.add_requested.connect(lambda: fired.append(True))

    point = QPoint(5, 5).toPointF()
    card.mousePressEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonPress,
            point,
            Qt.MouseButton.RightButton,
            Qt.MouseButton.RightButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    assert fired == []


def test_hovering_restyles_the_card(qapp):
    """Dashed and quiet at rest, filled on hover -- a hit target confirming itself."""
    card = AddCard("Add account")
    at_rest = card.styleSheet()

    point = QPoint(5, 5).toPointF()
    card.enterEvent(QEnterEvent(point, point, point))
    hovered = card.styleSheet()
    card.leaveEvent(QEvent(QEvent.Type.Leave))

    assert "dashed" in at_rest and "dashed" in hovered
    assert hovered != at_rest
    assert card.styleSheet() == at_rest


def test_the_dashes_are_wide_enough_to_be_dashes(qapp):
    """Qt scales a dash pattern by the pen width, so 1px dashed draws as 1px dots.

    At the subtle border color that is a line you have to look for, and on a dark card's
    top and left edges it was not visible at all -- which loses the outline that is the
    whole reason this reads as an empty slot rather than a card.
    """
    card = AddCard("Add account")

    assert ADD_CARD_BORDER_WIDTH > 1
    assert f"{ADD_CARD_BORDER_WIDTH}px dashed" in card.styleSheet()


def test_a_menu_opens_under_the_outline_not_the_gutter(qapp):
    """The widget starts at the drag gutter, the dashed outline 22px later.

    Anchored to the widget, the Combat tab's add-card menu opened that far left of the
    card the user had just clicked.
    """
    card = AddCard("Add another duel strategy")
    card.resize(300, 40)
    card.layout().activate()

    at = card.menu_position()

    assert at.x() == card.mapToGlobal(QPoint(GRIP_GUTTER, 0)).x()
    assert at.y() >= card.mapToGlobal(QPoint(0, card.height())).y()


def test_the_menu_is_at_least_as_wide_as_the_outline(qapp):
    card = AddCard("Add another duel strategy")
    card.resize(300, 40)
    card.layout().activate()

    menu = card.build_menu([("plus", "Short", lambda: None)])

    assert menu.minimumWidth() == 300 - GRIP_GUTTER
    assert menu.sizeHint().width() >= 300 - GRIP_GUTTER


def test_a_theme_switch_restyles_the_add_card(qapp, theme):
    card = AddCard("Add account")
    theme("light")
    card.refresh_theme_styles()
    light = card.styleSheet()

    theme("dark")
    card.refresh_theme_styles()

    assert card.styleSheet() != light
