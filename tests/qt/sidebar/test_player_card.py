"""The player card as a widget: the copy affordance, and a card drawn from a ``PlayerView``.

``test_player_card_text.py`` has everything decidable from a username and an index. The
copy tests moved from BartleBot's ``tests/gui/sidebar/test_player_card_widgets.py`` with
the card; the rest hold what the card does with a view, now that it holds no client
(Stage 8 of BartleBot #167). How a column of them is drawn, dragged and locked is
``test_player_column.py``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from PyQt6.QtCore import QEvent, QPoint, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest

from bartleby.qt.sidebar import player_card
from bartleby.qt.sidebar.player_card import PLACEHOLDER, CopyValue, PlayerCard, PlayerVerb
from bartleby.qt.theming.account_accent import account_accent
from bartleby.runtime.views import BADGE_ACCENT, CardBadge, PlayerRow, PlayerView

INSPECT = PlayerVerb("inspect", "Inspect", "search", "Inspect this player")
VERBS = {INSPECT.id: INSPECT}


def _view(hwnd: int = 1, slot: int = 1, **fields) -> PlayerView:
    return PlayerView(hwnd=hwnd, slot=slot, **fields)


# ---------------------------------------------------------------------------
# The copy affordance
# ---------------------------------------------------------------------------


@pytest.fixture
def clipboard(monkeypatch):
    """Capture what would go to the system clipboard, rather than writing to it."""
    written: list[str] = []
    monkeypatch.setattr("bartleby.qt.sidebar.player_card.pyperclip.copy", written.append)
    return written


def _click(widget) -> None:
    widget.mouseReleaseEvent(
        QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPoint(5, 5).toPointF(),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_clicking_a_value_copies_what_the_row_displays(qapp, clipboard):
    row = CopyValue("Click to copy this zone name")
    row.set_value("WizardCity/WC_Streets/Interior")

    _click(row)

    assert clipboard == ["WizardCity/WC_Streets/Interior"]


def test_a_row_whose_copy_form_differs_copies_the_copy_form(qapp, clipboard):
    """XYZ reads at one decimal and pastes as JSON at two — see ``xyz_clipboard_text``."""
    row = CopyValue("Click to copy this position as JSON")
    row.set_value("X=1.0  Y=2.0  Z=3.0", '{"x": 1.0, "y": 2.0, "z": 3.0}')

    _click(row)

    assert clipboard == ['{"x": 1.0, "y": 2.0, "z": 3.0}']


def test_a_copy_confirms_itself(qapp, clipboard):
    """There was no feedback at all before: you pressed ``Copy`` and nothing changed."""
    row = CopyValue("Click to copy")
    row.set_value("Colossus Boulevard")
    assert not row.confirming

    _click(row)

    assert row.confirming
    assert clipboard == ["Colossus Boulevard"]


def test_the_confirmation_goes_away_on_its_own(qapp):
    row = CopyValue("Click to copy")
    row.set_value("Colossus Boulevard")
    row.copy()

    QTest.qWait(player_card.COPY_CONFIRM_MS + 200)

    assert not row.confirming


def test_a_placeholder_row_copies_nothing_and_confirms_nothing(qapp, clipboard):
    """An em-dash pasted into a bot param is a silently wrong value."""
    row = CopyValue("Click to copy this zone name")

    assert row.display_text == PLACEHOLDER
    assert not row.copyable
    assert row.copy() is False

    _click(row)

    assert clipboard == []
    assert not row.confirming


# ---------------------------------------------------------------------------
# A card drawn from a view
# ---------------------------------------------------------------------------


def test_building_a_card_never_shows_a_parentless_widget(qapp, parentless_shows):
    PlayerCard(_view(rows=(PlayerRow("Holder", "idle"),), badges=(CardBadge("Leader", BADGE_ACCENT),)), VERBS)

    assert parentless_shows == []


def test_a_card_starts_with_every_row_uncopyable(qapp):
    """Nothing has been read yet, so there is nothing on the card worth pasting anywhere."""
    card = PlayerCard(_view(rows=(PlayerRow("XYZ", "X=—", PLACEHOLDER),)))

    assert list(card.rows) == ["Wizard", "Zone", "XYZ"]
    assert not any(row.copyable for row in card.rows.values())


def test_a_view_draws_its_name_zone_and_rows(qapp):
    card = PlayerCard(_view())

    card.show_view(
        _view(
            wizard_name="Luke SkullHunter",
            zone="WizardCity/WC_Streets/Interior",
            rows=(PlayerRow("XYZ", "X=1.0", '{"x": 1.0}', "Click to copy this position"),),
        )
    )

    assert card.rows["Wizard"].display_text == "Luke SkullHunter"
    assert card.rows["Zone"].display_text == "WizardCity/WC_Streets/Interior"
    assert card.rows["XYZ"].copy_text == '{"x": 1.0}'
    assert card.rows["XYZ"].toolTip() == "Click to copy this position"
    assert card.last_zone == "WizardCity/WC_Streets/Interior"


def test_a_name_in_streamer_mode_is_the_character_number(qapp):
    from bartleby.qt import streamer_mode

    streamer_mode.configure(lambda: True)
    try:
        card = PlayerCard(_view(slot=2, wizard_name="Luke SkullHunter"))
        assert card.rows["Wizard"].display_text == "Character 2"
    finally:
        streamer_mode.configure(lambda: False)


def test_a_loading_view_keeps_the_zone_drawn_last_and_draws_the_rest(qapp):
    """A client between zones has no zone worth reading; what is not read from the game still lands."""
    card = PlayerCard(_view(zone="Colossus Boulevard", rows=(PlayerRow("Holder", "following P1"),)))

    card.show_view(_view(loading=True, color_key=2, rows=(PlayerRow("Holder", "porting to P1"),)))

    assert card.rows["Zone"].display_text == "Colossus Boulevard"
    assert card.last_zone == "Colossus Boulevard"
    assert card.rows["Holder"].display_text == "porting to P1"
    assert card.color_key == 2


def test_a_row_a_later_view_adds_goes_under_the_rows_already_there(qapp):
    card = PlayerCard(_view(rows=(PlayerRow("First", "1"),)))

    card.show_view(_view(rows=(PlayerRow("First", "1"), PlayerRow("Second", "2"))))

    assert list(card.rows) == ["Wizard", "Zone", "First", "Second"]


def test_the_stripe_and_the_pill_follow_the_view_s_color(qapp):
    """The identity arrives late, so the color arrives in every view, never once."""
    card = PlayerCard(_view())
    assert card.color_key is None
    assert "border-left" not in card.styleSheet()

    card.show_view(_view(color_key=3, account_label="Main"))

    assert card.color_key == 3
    assert account_accent(3) in card.styleSheet()
    assert card._pill.color_key == 3
    assert "Main" in card._pill.toolTip()


def test_a_hooked_card_wears_its_slot_and_an_unhooked_one_none(qapp):
    assert PlayerCard(_view(slot=2)).pill_text == "P2"
    assert PlayerCard(_view(slot=1, unhooked=True)).pill_text is None


def test_a_new_slot_renumbers_the_card_in_place(qapp):
    card = PlayerCard(_view(slot=1))

    card.show_view(_view(slot=3))

    assert (card.index, card.pill_text) == (2, "P3")


def test_badges_are_drawn_on_the_header_and_follow_the_view(qapp):
    card = PlayerCard(_view(badges=(CardBadge("Leader", BADGE_ACCENT, "Leads the team"),)))
    assert card.badge_texts == ["Leader"]

    card.show_view(_view())

    assert card.badge_texts == []


def test_a_product_verb_names_itself_with_the_card_s_window(qapp):
    card = PlayerCard(_view(hwnd=42, verbs=("inspect", "unknown")), VERBS)
    seen: list[tuple[int, str]] = []
    card.verb_triggered.connect(lambda hwnd, verb: seen.append((hwnd, verb)))

    assert [v.label for v in card._verbs()] == ["Inspect", "Unhook"]
    card.action_button("Inspect").click()

    assert seen == [(42, "inspect")]
    assert card.verb_ids == ("inspect",), "a verb the product did not describe is not offered"


def test_unhook_asks_by_index_and_rehook_by_window(qapp):
    hooked = PlayerCard(_view(hwnd=7, slot=2))
    unhooked = PlayerCard(_view(hwnd=9, slot=1, unhooked=True))
    asked: list[tuple[str, int]] = []
    hooked.unhook_requested.connect(lambda i: asked.append(("unhook", i)))
    unhooked.rehook_requested.connect(lambda h: asked.append(("rehook", h)))

    hooked.action_button("Unhook").click()
    unhooked.action_button("Rehook").click()

    assert asked == [("unhook", 1), ("rehook", 9)]
    assert "Sample" in unhooked.action_button("Rehook").toolTip(), "the product's name, never a literal"


def test_the_body_of_a_player_card_is_not_a_button(qapp):
    """MEMBER mode, not MANAGE: there is no "open this player" for a body click to mean."""
    card = PlayerCard(_view())

    assert not card.has_body_verb
    assert card.pins_chrome and not card.refuses_when_unfit


def test_the_card_imports_nothing_from_wizwalker():
    """It draws a view and holds no client, so nothing in it can reach one."""
    tree = ast.parse(Path(player_card.__file__).read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    modules += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]

    assert not [m for m in modules if m.split(".")[0] in ("wizwalker", "wizsprinter")]
