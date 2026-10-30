"""An answer card: its pill, its radio, and how it is chosen."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel

from bartleby.qt.widgets.cards.start_card import StartCard


def test_a_pill_handed_in_sits_beside_the_name(qapp):
    card = StartCard("A template", "What it is for.", pill=QLabel("Official"))

    assert card.pill_text == "Official"


def test_a_card_with_no_pill_says_none(qapp):
    assert StartCard("Quick setup", "What it asks.").pill_text == ""


def test_a_radio_card_checks_its_radio_while_selected(qapp):
    card = StartCard("Quick setup", "What it asks.", radio=True)

    card.set_selected(True)
    assert card.radio.isChecked()
    card.set_selected(False)
    assert not card.radio.isChecked()


def test_focus_chooses_the_card(qapp):
    card = StartCard("Quick setup", "What it asks.")
    chosen = []
    card.chosen.connect(lambda: chosen.append(True))

    card.focusInEvent(None)

    assert chosen == [True]
