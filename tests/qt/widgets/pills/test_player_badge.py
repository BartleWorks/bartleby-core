"""``PlayerBadge``: the slot is the text, and the account is the color, or no color at all.

Moved from BartleBot's ``tests/gui/widgets/pills/test_player_pills.py`` with the badge.
"""

from bartleby.qt.theming.account_accent import account_accent, account_tint
from bartleby.qt.theming.theme import THEME
from bartleby.qt.widgets.pills.player_badge import PlayerBadge


def test_an_uncolored_badge_is_the_neutral_gray_pill(qapp):
    badge = PlayerBadge(2)

    assert badge.text() == "P2"
    assert THEME.pill_neutral_bg in badge.styleSheet()
    assert account_accent(1) not in badge.styleSheet()


def test_a_colored_badge_is_outlined_and_tinted_and_names_the_account_in_its_tooltip(qapp):
    badge = PlayerBadge(1)

    badge.set_color_key(3)
    badge.set_account_label("Main")

    assert account_accent(3) in badge.styleSheet()
    assert account_tint(3) in badge.styleSheet()
    assert badge.text() == "P1"
    assert "Main" in badge.toolTip()


def test_a_badge_can_be_pointed_at_another_slot_keeping_its_color(qapp):
    badge = PlayerBadge(1)
    badge.set_color_key(2)

    badge.set_player(3)

    assert badge.text() == "P3"
    assert badge.player == 3
    assert badge.color_key == 2
    assert account_accent(2) in badge.styleSheet()
