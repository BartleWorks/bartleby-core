"""Tests for CombatStrategyDialog — the Save/Cancel boundary, as a widget.

This file replaces ``test_strategy_editor_identity.py``, which existed for a
defect that no longer has a shape. Every editor used to autosave on a 500ms
debounce, so a save could land *after* the user had selected a different
strategy, and its ``changed`` signal had to carry an id to keep the late write
from being applied to the wrong entry. #4 retired the debounce: editors write
through to a working copy, and nothing reaches disk until Save. There is no
window across which the selection can move, so there is no id to carry.

What is worth testing now is the boundary itself in its Qt form — that the
editors mutate the working set and only the working set, that Cancel writes
nothing, and that Save writes everything — plus the two things the dialog knows
that the working set does not: that removing a shipped override is a *revert*,
and that promoting an assignment's DSL makes a Player Strategy the sibling tab
can see.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QLabel, QMessageBox, QSplitter

from bartleby.qt.combat.assignment_card import CUSTOM_DATA, EDIT_VERB, strategy_menu_entries
from bartleby.qt.combat.deck_link import DeckClientLink
from bartleby.qt.combat.dialog import KIND_LABELS, NEW_NAMES, DialogMode, KindPage, tabs_for
from bartleby.qt.testing.picker_helpers import pick
from bartleby.qt.widgets.cards.archmastery_editor import APP_SETTING_ON
from bartleby.qt.widgets.layout.scroll_gutter import is_gutter_reserved
from bartleby.qt.widgets.pills.pill import AccentPill, NeutralPill
from bartleby.runtime.combat.strategy_workset import StrategyWorkingSet
from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import (
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)
from tests.qt.combat.library import SampleStrategySettings, library_roots, open_dialog, save, stored
from tests.runtime.combat.owners import FakeOwner


@pytest.fixture
def roots(tmp_path):
    return library_roots(tmp_path)


_dialog = open_dialog


# ---------------------------------------------------------------------------
# The boundary
# ---------------------------------------------------------------------------


def test_typing_in_an_editor_reaches_the_working_set_and_nothing_else(qapp, roots):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save(pcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)

    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    assert dlg._ws.get(StrategyKind.PLAYER, pcs.id).dsl == "pass"
    assert stored(StrategyKind.PLAYER, roots)[0].dsl == "blade"


def test_save_writes_every_change_at_once(qapp, roots):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save(pcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    dlg._on_commit()

    assert stored(StrategyKind.PLAYER, roots)[0].dsl == "pass"
    assert not dlg._ws.is_dirty


def test_cancel_writes_nothing(qapp, roots):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save(pcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Discard):
        dlg.reject()

    assert stored(StrategyKind.PLAYER, roots)[0].dsl == "blade"


def test_a_clean_cancel_asks_nothing(qapp, roots):
    save(PlayerCombatStrategy(name="Blade"), roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)

    with patch.object(QMessageBox, "question") as asked:
        dlg.reject()

    assert not asked.called


def test_declining_the_discard_prompt_keeps_the_dialog_open(qapp, roots):
    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save(pcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Cancel):
        dlg.reject()

    assert dlg._ws.is_dirty
    assert dlg.result() == 0


def test_a_save_that_could_not_write_keeps_the_dialog_open(qapp, roots, monkeypatch):
    """commit() logs and skips a failed write; closing anyway would lose it silently."""
    from bartleby.shared import strategy_library  # noqa: PLC0415

    pcs = PlayerCombatStrategy(name="Blade", dsl="blade")
    save(pcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit.setPlainText("pass")

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(strategy_library, "save", boom)
    with patch.object(QMessageBox, "warning") as warned:
        dlg._on_commit()

    assert warned.called
    assert dlg.result() == 0
    assert dlg._ws.is_dirty


# ---------------------------------------------------------------------------
# Pick mode
# ---------------------------------------------------------------------------


def test_use_returns_the_selection_and_commits_the_edits(qapp, roots):
    shipped, user = roots
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save(dcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, current_id=dcs.id)
    dlg._pages[StrategyKind.DUEL].about._name_edit.setText("Renamed")

    dlg._on_commit()

    assert dlg.selected_id() == dcs.id
    assert stored(StrategyKind.DUEL, roots)[0].name == "Renamed"


def test_use_built_in_default_returns_none_and_says_so(qapp, roots):
    save(DuelCombatStrategy(name="Frenzy AoE"), roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK)

    dlg._on_use_none()

    assert dlg.selected_id() is None
    assert dlg.used_built_in_default()


def test_a_pick_shows_only_the_kind_that_was_asked_for(qapp, roots):
    """A Player Strategy tab while picking a duel is somewhere to edit things, and Pick
    edits nothing (#123)."""
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, current_id=dcs.id)

    dlg._on_commit()

    assert dlg.tab_kinds() == [StrategyKind.DUEL]
    assert dlg.selected_id() == dcs.id


def test_a_team_size_opens_on_the_duels_that_size_can_run_until_the_filter_changes(qapp, roots):
    """Settings › Combat's Team card asks for two (epic #201, Stage 5): a duel for one
    player, or one that needs four, is not offered until the chip is taken off."""
    solo = DuelCombatStrategy(name="Solo", min_players=1, max_players=1)
    pair = DuelCombatStrategy(name="Pair", min_players=2, max_players=4)
    four = DuelCombatStrategy(name="Four", min_players=4, max_players=4)
    for duel in (solo, pair, four):
        save(duel, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, allow_none=False, team_size=2)
    page = dlg.page(StrategyKind.DUEL)

    assert page.visible_ids() == [pair.id]
    assert page.filter_bar.chip_texts() == ["Team of 2"]

    page.filter_bar.clear_facets()

    assert sorted(page.visible_ids()) == sorted([solo.id, pair.id, four.id])


def test_the_use_button_says_what_the_caller_falls_to(qapp, roots):
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, none_label="(bot default)", none_tooltip="Tip")

    assert dlg._none_btn.text() == "Use (bot default)"
    assert dlg._none_btn.toolTip() == "Tip"


def test_a_pick_that_may_not_be_empty_refuses_to_close_with_nothing(qapp, roots):
    save(DuelCombatStrategy(name="Frenzy AoE"), roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, allow_none=False)
    dlg._pages[StrategyKind.DUEL].select(None)

    with patch.object(QMessageBox, "information") as told:
        dlg._on_commit()

    assert told.called
    assert dlg.result() == 0


def test_manage_mode_offers_no_built_in_default_button(qapp, roots):
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.LIBRARY)

    assert not hasattr(dlg, "_none_btn")


# ---------------------------------------------------------------------------
# New / duplicate / delete-or-revert
# ---------------------------------------------------------------------------


def test_new_creates_selects_and_names_uniquely(qapp, roots):
    save(PlayerCombatStrategy(name=NEW_NAMES[StrategyKind.PLAYER]), roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)
    page = dlg._pages[StrategyKind.PLAYER]

    page._on_new()

    made = dlg._ws.get(StrategyKind.PLAYER, page.selected_id())
    assert made.name == f"{NEW_NAMES[StrategyKind.PLAYER]} (2)"
    assert page._editor.strategy() is made


def test_duplicate_selects_the_copy_not_the_original(qapp, roots):
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    page = dlg._pages[StrategyKind.DUEL]

    page._on_duplicate()

    assert page.selected_id() != dcs.id
    assert dlg._ws.get(StrategyKind.DUEL, page.selected_id()).name == "Frenzy AoE (2)"


def test_a_shipped_override_offers_revert_rather_than_delete(qapp, roots):
    """Deleting only removes the user copy, so on one of these the act is revert."""
    shipped, user = roots
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save(dcs, shipped)
    save(dcs, user)  # the override
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    assert dlg._pages[StrategyKind.DUEL]._del_btn.text() == "Revert to default"


def test_a_user_only_strategy_offers_delete(qapp, roots):
    dcs = DuelCombatStrategy(name="Mine")
    save(dcs, roots[1])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    assert dlg._pages[StrategyKind.DUEL]._del_btn.text() == "Delete"


def test_reverting_an_override_restores_the_shipped_version_on_save(qapp, roots):
    shipped, user = roots
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save(dcs, shipped)
    save(dcs.model_copy(update={"name": "Mine"}), user)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        dlg._pages[StrategyKind.DUEL]._on_delete()
    dlg._on_commit()

    assert stored(StrategyKind.DUEL, roots)[0].name == "Frenzy AoE"


def test_an_untouched_built_in_offers_no_delete(qapp, roots):
    """Removing only touches the user root, so a deleted built-in was back on the next open (#124)."""
    dcs = DuelCombatStrategy(name="Frenzy AoE")
    save(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    button = dlg._pages[StrategyKind.DUEL]._del_btn

    assert not button.isEnabled()
    assert "Duplicate" in button.toolTip()


def test_editing_a_built_in_offers_revert_before_anything_is_saved(qapp, roots):
    pcs = PlayerCombatStrategy(name="Support", dsl="pass")
    save(pcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    page = dlg._pages[StrategyKind.PLAYER]

    page.about._description_edit.setText("tweaked")

    assert page._del_btn.isEnabled()
    assert page._del_btn.text() == "Revert to default"


def test_a_revert_keeps_the_entry_listed_selected_and_loaded(qapp, roots):
    """#124's fourth point: the reverted entry used to vanish until the dialog was reopened."""
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Support", dsl="pass")
    save(pcs, shipped)
    save(pcs.model_copy(update={"name": "Mine"}), user)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    page = dlg._pages[StrategyKind.PLAYER]

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
        page._on_delete()

    assert page.selected_id() == pcs.id
    assert page.card_for(pcs.id).source_text == "Official"
    assert page.about._name_edit.text() == "Support"
    assert not page._del_btn.isEnabled()


@pytest.mark.parametrize(
    ("where", "label"),
    [("shipped", "Official"), ("override", "Official (modified)"), ("user", "Custom")],
)
def test_every_card_says_where_its_entry_comes_from(qapp, roots, where, label):
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Support")
    if where in ("shipped", "override"):
        save(pcs, shipped)
    if where in ("override", "user"):
        save(pcs.model_copy(update={"dsl": "pass"}), user)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)

    assert dlg._pages[StrategyKind.PLAYER].card_for(pcs.id).source_text == label


@pytest.mark.parametrize(
    ("where", "label"),
    [("shipped", "Official"), ("override", "Official (modified)"), ("user", "Custom")],
)
def test_every_deck_card_says_where_its_deck_comes_from(qapp, roots, where, label):
    """Decks shipped for the first time in #212; their cards wear the pill the other kinds do."""
    shipped, user = roots
    deck = DeckPreset(name="Feint Support", deck=DeckData(normal={"Feint": 3}))
    if where in ("shipped", "override"):
        save(deck, shipped)
    if where in ("override", "user"):
        save(deck.model_copy(update={"description": "mine"}), user)
    dlg = _dialog(roots, kind=StrategyKind.DECK)

    assert dlg._pages[StrategyKind.DECK].card_for(deck.id).source_text == label


def test_the_source_pill_follows_an_unsaved_edit(qapp, roots):
    pcs = PlayerCombatStrategy(name="Support")
    save(pcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)
    page = dlg._pages[StrategyKind.PLAYER]

    page.about._description_edit.setText("tweaked")

    assert page.card_for(pcs.id).source_text == "Official (modified)"


@pytest.mark.parametrize(
    ("where", "pill_type", "filled"),
    [("shipped", AccentPill, True), ("override", AccentPill, False), ("user", NeutralPill, None)],
)
def test_the_source_pill_is_weighted_like_a_button(qapp, roots, where, pill_type, filled):
    """Official filled with the accent, modified Official outlined in it, custom gray."""
    shipped, user = roots
    pcs = PlayerCombatStrategy(name="Support")
    if where in ("shipped", "override"):
        save(pcs, shipped)
    if where in ("override", "user"):
        save(pcs.model_copy(update={"dsl": "pass"}), user)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)
    card = dlg._pages[StrategyKind.PLAYER].card_for(pcs.id)

    pills = [p for p in card.findChildren(QLabel) if isinstance(p, AccentPill | NeutralPill)]
    assert [type(p) for p in pills] == [pill_type]
    if filled is not None:
        assert pills[0].filled is filled


def test_a_source_pill_edit_keeps_the_card_height(qapp, roots):
    """The filled and outlined pills are one size, so the card does not jump on an edit."""
    assert AccentPill("Official", filled=True).sizeHint().height() == AccentPill("Official").sizeHint().height()
    assert AccentPill("Official").sizeHint().height() == NeutralPill("Custom").sizeHint().height()


def _row(page, uuid):
    return next((item, card) for item, card in page._card_map if item.data(Qt.ItemDataRole.UserRole) == uuid)


def test_a_row_is_as_tall_as_its_card_needs_at_the_columns_width(qapp, roots):
    """Not the card's own size hint, which wraps the name at a width Qt guesses.

    A one-line name was given a two-line row, and the slack spread between the card's
    lines — so the card's spacing visibly changed when an edit made the name wrap.
    """
    pcs = PlayerCombatStrategy(name="Frenzy Blade Stack Hitter With A Long Name", description="tank and spank")
    save(pcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)
    page = dlg._pages[StrategyKind.PLAYER]
    splitter = page.findChild(QSplitter)
    dlg.resize(1400, 720)
    dlg.show()
    qapp.processEvents()

    item, card = _row(page, pcs.id)
    wide = item.sizeHint().height()
    assert wide == card.heightForWidth(card.width())

    splitter.setSizes([splitter.widget(0).minimumSizeHint().width(), 2000])
    qapp.processEvents()

    item, card = _row(page, pcs.id)
    assert item.sizeHint().height() == card.heightForWidth(card.width())
    assert item.sizeHint().height() > wide
    dlg.close()


def test_a_card_never_asks_for_more_width_than_the_column(qapp, roots):
    """A row asking for its card's preferred width gives the list a horizontal scrollbar."""
    pcs = PlayerCombatStrategy(name="Frenzy Blade Stack Hitter With A Long Name")
    save(pcs, roots[0])
    save(pcs.model_copy(update={"dsl": "pass"}), roots[1])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)
    page = dlg._pages[StrategyKind.PLAYER]
    dlg.resize(dlg.minimumSize())
    dlg.show()
    qapp.processEvents()

    assert page._list.horizontalScrollBar().maximum() == 0
    dlg.close()


def test_no_row_is_wider_than_the_column_even_when_its_card_cannot_fit(qapp, roots):
    """A list's rows are as wide as its widest: one card that could not fit clipped every card's star."""
    wide = PlayerCombatStrategy(name="Frenzy" * 40)
    save(wide, roots[1])
    save(PlayerCombatStrategy(name="Short"), roots[1])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)
    page = dlg._pages[StrategyKind.PLAYER]
    dlg.resize(dlg.minimumSize())
    dlg.show()
    qapp.processEvents()

    viewport = page._list.viewport().width()
    for item, _card in page._card_map:
        assert item.sizeHint().width() <= viewport
    assert page._list.horizontalScrollBar().maximum() == 0
    assert page._list.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    dlg.close()


def test_every_card_fits_its_column_after_the_column_narrows(qapp, roots):
    """The deck tab opened with every card overhanging its column, pill and star cut off (#170).

    A list view lays its rows out once by default, so rows laid out at a wider width kept
    it: a tab first laid out while hidden, at whatever width its page had then.
    """
    save(PlayerCombatStrategy(name="Hitter"), roots[1])
    save(PlayerCombatStrategy(name="Support"), roots[1])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)
    page = dlg.page(StrategyKind.PLAYER)
    splitter = page.findChild(QSplitter)
    dlg.resize(1400, 720)
    dlg.show()
    splitter.setSizes([1000, 400])
    qapp.processEvents()

    splitter.setSizes([splitter.widget(0).minimumSizeHint().width(), 2000])
    QTest.qWait(50)  # the relayout a resize asks for is deferred, as Qt defers every one

    viewport = page._list.viewport().width()
    for _item, card in page._card_map:
        assert card.geometry().right() < viewport
    dlg.close()


def test_a_name_with_one_long_word_is_elided_to_the_column(qapp, roots):
    """Word wrap cannot break a word, so the card elides it rather than outgrowing the column (#118)."""
    name = "Frenzy" * 40
    save(PlayerCombatStrategy(name=name), roots[1])
    dlg = _dialog(roots, kind=StrategyKind.PLAYER)
    page = dlg._pages[StrategyKind.PLAYER]
    dlg.resize(dlg.minimumSize())
    dlg.show()
    qapp.processEvents()

    [(_item, card)] = page._card_map
    assert card.minimumSizeHint().width() <= page._list.viewport().width()
    assert card._name_lbl.text() == name
    assert card._name_lbl.shown_text().endswith("…")
    assert card._name_lbl.toolTip() == name
    dlg.close()


def test_a_declined_delete_removes_nothing(qapp, roots):
    dcs = DuelCombatStrategy(name="Mine")
    save(dcs, roots[1])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
        dlg._pages[StrategyKind.DUEL]._on_delete()

    assert not dlg._ws.is_dirty


# ---------------------------------------------------------------------------
# The detail pane's two extra lines
# ---------------------------------------------------------------------------


def test_the_detail_pane_says_what_uses_this_strategy(qapp, roots):
    shipped, _user = roots
    pcs = PlayerCombatStrategy(name="Blade")
    dcs = DuelCombatStrategy(
        name="Boss", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    save(pcs, shipped)
    save(dcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=pcs.id)

    assert dlg._pages[StrategyKind.PLAYER].about.usage_text == "Used by 'Boss — P1'"


def test_the_usage_line_counts_a_link_made_a_minute_ago(qapp, roots):
    """The reason it is counted against the working set rather than disk."""
    shipped, _user = roots
    pcs = PlayerCombatStrategy(name="Blade")
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save(pcs, shipped)
    save(dcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    row = dlg._pages[StrategyKind.DUEL]._editor.cards[0]
    row.choose_strategy(pcs.id)

    dlg._pages[StrategyKind.PLAYER].select(pcs.id)

    assert dlg._pages[StrategyKind.PLAYER].about.usage_text == "Used by 'Boss — P1'"


# ---------------------------------------------------------------------------
# Promotion: the row asks, the dialog names and creates
# ---------------------------------------------------------------------------


def test_promoting_a_rows_dsl_makes_a_player_strategy_and_links_it(qapp, roots):
    shipped, _user = roots
    dcs = DuelCombatStrategy(
        name="Boss", player_assignments=[PlayerAssignment(players=[1], dsl="blade", schools=["Ice"])]
    )
    save(dcs, shipped)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    row = dlg._pages[StrategyKind.DUEL]._editor.cards[0]

    with patch("bartleby.qt.combat.dialog.QInputDialog.getText", return_value=("Ice Blade", True)):
        row._promote_btn.click()

    made = next(p for p in dlg._ws.players if p.name == "Ice Blade")
    assert made.dsl == "blade"
    assert made.schools == ["Ice"]
    assert row.assignment().player_combat_strategy_id == made.id
    assert not row.is_custom()


def test_a_cancelled_promotion_changes_nothing(qapp, roots):
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1], dsl="blade")])
    save(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    row = dlg._pages[StrategyKind.DUEL]._editor.cards[0]

    with patch("bartleby.qt.combat.dialog.QInputDialog.getText", return_value=("", False)):
        row._promote_btn.click()

    assert dlg._ws.players == []
    assert row.assignment().player_combat_strategy_id is None


def test_a_player_strategy_made_on_the_sibling_tab_appears_in_the_selector(qapp, roots):
    """What retired the modal picker-on-a-picker."""
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    dlg._pages[StrategyKind.PLAYER]._on_new()

    dlg._tabs.setCurrentWidget(dlg._pages[StrategyKind.DUEL])

    row = dlg._pages[StrategyKind.DUEL]._editor.cards[0]
    assert NEW_NAMES[StrategyKind.PLAYER] in [label for label, _data in strategy_menu_entries(dlg._ws.players)]
    assert row.is_expanded() is False  # naming a strategy elsewhere unfolds nothing here


# ---------------------------------------------------------------------------
# The pencil: from an assignment to the Player Strategy it links
# ---------------------------------------------------------------------------


def _linked_duel(roots) -> tuple[DuelCombatStrategy, PlayerCombatStrategy]:
    shipped, _user = roots
    other = PlayerCombatStrategy(name="Aoe")
    pcs = PlayerCombatStrategy(name="Blade", dsl="any<blade>")
    dcs = DuelCombatStrategy(
        name="Boss", player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=pcs.id)]
    )
    save(other, shipped)
    save(pcs, shipped)
    save(dcs, shipped)
    return dcs, pcs


def test_the_pencil_opens_the_linked_strategy_on_the_player_tab(qapp, roots):
    dcs, pcs = _linked_duel(roots)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    player = dlg._pages[StrategyKind.PLAYER]

    dlg._pages[StrategyKind.DUEL]._editor.cards[0].action_button(EDIT_VERB).click()

    assert dlg._tabs.currentWidget() is player
    assert player.selected_id() == pcs.id
    assert player.about._name_edit.text() == "Blade"


def test_the_pencil_puts_the_caret_in_the_dsl_box(qapp, roots):
    """Not the filter, which is where the tab switch alone would leave it."""
    dcs, _pcs = _linked_duel(roots)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    dlg.show()

    dlg._pages[StrategyKind.DUEL]._editor.cards[0].action_button(EDIT_VERB).click()

    assert dlg.focusWidget() is dlg._pages[StrategyKind.PLAYER]._editor._dsl_edit
    dlg.close()


def test_the_pencil_clears_a_filter_that_would_hide_the_strategy(qapp, roots):
    """``_apply_filter`` keeps a hidden row selected, so a stale query would load the
    strategy with no visible card saying which one it is."""
    dcs, pcs = _linked_duel(roots)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    player = dlg._pages[StrategyKind.PLAYER]
    player.filter_bar.search.setText("Aoe")

    dlg._pages[StrategyKind.DUEL]._editor.cards[0].action_button(EDIT_VERB).click()

    assert player.filter_bar.search.text() == ""
    assert player.selected_id() == pcs.id
    assert not player._list.currentItem().isHidden()


def test_a_pick_grays_the_pencil_it_has_nowhere_to_send(qapp, roots):
    """Pick has only the Duel tab, so there is no Player tab for the pencil to open."""
    dcs, _pcs = _linked_duel(roots)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, mode=DialogMode.PICK, current_id=dcs.id)

    pencil = dlg._pages[StrategyKind.DUEL]._editor.cards[0].action_button(EDIT_VERB)

    assert not pencil.isHidden()
    assert not pencil.isEnabled()


def test_the_duel_tab_keeps_its_selection_across_the_round_trip(qapp, roots):
    dcs, _pcs = _linked_duel(roots)
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    duel = dlg._pages[StrategyKind.DUEL]

    duel._editor.cards[0].action_button(EDIT_VERB).click()
    dlg._tabs.setCurrentWidget(duel)

    assert duel.selected_id() == dcs.id
    assert duel.about._name_edit.text() == "Boss"


@pytest.mark.parametrize("kind", list(KIND_LABELS))
def test_both_panes_reserve_their_scrollbar_gutter(qapp, roots, kind):
    """A card folding open, or a filter keystroke, must not narrow every card by a bar's width."""
    dlg = _dialog(roots)
    page = dlg._pages[kind]

    assert is_gutter_reserved(page._scroll)
    assert is_gutter_reserved(page._list)


# ---------------------------------------------------------------------------
# Custom DSL, one level deeper
# ---------------------------------------------------------------------------


def test_a_fresh_assignment_shows_no_dsl_box(qapp, roots):
    """The Assemble tier: no monospace box in front of a user who will never type in one."""
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    row = dlg._pages[StrategyKind.DUEL]._editor.cards[0]
    assert not row.is_custom()
    assert row._dsl_edit.isHidden()
    assert row._promote_btn.isHidden()


def test_choosing_custom_dsl_opens_the_box_and_offers_to_name_it(qapp, roots):
    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)
    row = dlg._pages[StrategyKind.DUEL]._editor.cards[0]

    row.choose_strategy(CUSTOM_DATA)
    assert row.is_custom()
    assert row._promote_btn.isHidden()  # nothing to name yet

    row._dsl_edit.setPlainText("blade")
    assert not row._promote_btn.isHidden()


# ---------------------------------------------------------------------------
# Deck presets
# ---------------------------------------------------------------------------


def _deck_page(roots, preset: DeckPreset):
    save(preset, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DECK, current_id=preset.id)
    return dlg, dlg._pages[StrategyKind.DECK]


def test_the_deck_tab_edits_the_presets_description(qapp, roots):
    preset = DeckPreset(name="Storm 60")
    dlg, page = _deck_page(roots, preset)

    page.about._description_edit.setText("for the Storm wizard")

    assert dlg._ws.get(StrategyKind.DECK, preset.id).description == "for the Storm wizard"


def test_the_deck_tab_lists_the_cards_the_preset_holds(qapp, roots):
    """The same rows the sidebar's deck card unfolds into — grouped, ordered by school,
    and art where the user has extracted any. See tests/qt/widgets/cards/test_deck_card_list.py."""
    preset = DeckPreset(name="Storm 60", deck=DeckData(normal={"Test Card": 3}, item={"Test Item": 1}))
    _dlg, page = _deck_page(roots, preset)

    assert page._editor.cards.row_texts() == ["Normal Cards", "×3 Test Card", "Item Cards", "×1 Test Item"]


def test_the_deck_tab_edits_the_archmastery_override(qapp, roots):
    """The control the library page was missing: a preset's archmastery answer was
    reachable only by loading it into a player slot first."""
    preset = DeckPreset(name="Storm 60", deck=DeckData(normal={"Test Card": 1}, archmastery_school="Storm"))
    dlg, page = _deck_page(roots, preset)

    next(b for b, value in page._editor.archmastery._buttons if value is True).click()

    assert dlg._ws.get(StrategyKind.DECK, preset.id).deck.apply_archmastery is True


def test_an_archmastery_edit_is_a_change_the_dialog_can_save_or_discard(qapp, roots):
    preset = DeckPreset(name="Storm 60", deck=DeckData(normal={"Test Card": 1}, archmastery_school="Storm"))
    dlg, page = _deck_page(roots, preset)
    assert not dlg._ws.is_dirty

    next(b for b, value in page._editor.archmastery._buttons if value is False).click()

    assert dlg._ws.changes().modified == [preset.id]


def test_an_archmastery_edit_reaches_disk_only_on_save(qapp, roots):
    preset = DeckPreset(name="Storm 60", deck=DeckData(normal={"Test Card": 1}, archmastery_school="Storm"))
    dlg, page = _deck_page(roots, preset)
    next(b for b, value in page._editor.archmastery._buttons if value is True).click()

    on_disk = next(p for p in stored(StrategyKind.DECK, roots) if p.id == preset.id)
    assert on_disk.deck.apply_archmastery is None, "nothing below the dialog writes"

    dlg._on_commit()

    saved = next(p for p in stored(StrategyKind.DECK, roots) if p.id == preset.id)
    assert saved.deck.apply_archmastery is True


def test_every_kind_gets_a_page(qapp, roots):
    dlg = _dialog(roots)

    assert dlg._tabs.count() == 3
    assert all(isinstance(dlg._tabs.widget(i), KindPage) for i in range(3))


# ---------------------------------------------------------------------------
# The About section — one arrangement of the same two fields on all three tabs
#
# They used to be drawn three ways: label-above on the Player tab, label-beside
# on the Deck tab, and split across two widgets on the Duel tab, where the name
# belonged to a wrapper pane and the description to the DuelConfig underneath it.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", list(KIND_LABELS))
def test_every_tab_has_an_about_section(qapp, roots, kind):
    dlg = _dialog(roots, kind=kind)

    assert dlg._pages[kind].about is not None


def _one_of_each(roots):
    """A saved entry of every kind, so each tab has something selected."""
    shipped, _user = roots
    made = {
        StrategyKind.DUEL: DuelCombatStrategy(name="Boss"),
        StrategyKind.PLAYER: PlayerCombatStrategy(name="Blade"),
        StrategyKind.DECK: DeckPreset(name="Storm 60"),
    }
    save(made[StrategyKind.DUEL], shipped)
    save(made[StrategyKind.PLAYER], shipped)
    save(made[StrategyKind.DECK], shipped)
    return made


@pytest.mark.parametrize("kind", list(KIND_LABELS))
def test_the_about_section_loads_the_selected_entry_on_every_tab(qapp, roots, kind):
    entry = _one_of_each(roots)[kind]
    dlg = _dialog(roots, kind=kind, current_id=entry.id)

    assert dlg._pages[kind].about._name_edit.text() == entry.name


@pytest.mark.parametrize("kind", list(KIND_LABELS))
def test_editing_the_name_writes_through_on_every_tab(qapp, roots, kind):
    entry = _one_of_each(roots)[kind]
    dlg = _dialog(roots, kind=kind, current_id=entry.id)

    dlg._pages[kind].about._name_edit.setText("Renamed")

    assert dlg._ws.get(kind, entry.id).name == "Renamed"


@pytest.mark.parametrize("kind", list(KIND_LABELS))
def test_editing_the_description_writes_through_on_every_tab(qapp, roots, kind):
    entry = _one_of_each(roots)[kind]
    dlg = _dialog(roots, kind=kind, current_id=entry.id)

    dlg._pages[kind].about._description_edit.setText("what it is for")

    assert dlg._ws.get(kind, entry.id).description == "what it is for"


def test_an_emptied_name_leaves_the_last_one_standing(qapp, roots):
    """A nameless entry is a nameless card in the list with no way back to it."""
    entry = _one_of_each(roots)[StrategyKind.PLAYER]
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=entry.id)

    dlg._pages[StrategyKind.PLAYER].about._name_edit.setText("")

    assert dlg._ws.get(StrategyKind.PLAYER, entry.id).name == "Blade"


def test_loading_an_entry_is_not_itself_an_edit(qapp, roots):
    """``setText`` emits ``textChanged``; the suppress guard is the only thing keeping a
    freshly opened strategy from being reported dirty."""
    entry = _one_of_each(roots)[StrategyKind.DUEL]
    dlg = _dialog(roots, kind=StrategyKind.DUEL)
    page = dlg._pages[StrategyKind.DUEL]
    seen = []
    page.about.changed.connect(lambda: seen.append(True))

    page.select(entry.id)

    assert seen == []


def test_the_about_section_carries_the_origin_note_for_a_shipped_entry(qapp, roots):
    entry = _one_of_each(roots)[StrategyKind.DUEL]
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=entry.id)

    assert "Ships with Sample" in dlg._pages[StrategyKind.DUEL].about.origin_text


def test_a_user_owned_entry_has_no_origin_note_to_make(qapp, roots):
    dlg = _dialog(roots, kind=StrategyKind.DUEL)
    page = dlg._pages[StrategyKind.DUEL]

    page._on_new()

    assert page.about.origin_text == ""


def test_deselecting_empties_the_about_section_with_the_editor(qapp, roots):
    entry = _one_of_each(roots)[StrategyKind.PLAYER]
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=entry.id)
    page = dlg._pages[StrategyKind.PLAYER]

    page.select(None)

    assert page.about._name_edit.text() == ""
    assert page.about.usage_text == ""


# ---------------------------------------------------------------------------
# Account colors reach the duel tab's slot pills and combat-order chips (#40)
# ---------------------------------------------------------------------------


def test_the_slot_colors_the_dialog_was_given_reach_the_assignment_cards(qapp, roots):
    from bartleby.qt.theming.account_accent import account_accent  # noqa: PLC0415

    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save(dcs, roots[0])

    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id, player_color_keys=[4, 5, 6, 7])

    card = dlg._pages[StrategyKind.DUEL]._editor.cards[0]
    assert account_accent(4) in card.pills.pills[0].styleSheet()


def test_a_dialog_given_no_colors_draws_uncolored_pills(qapp, roots):
    """Authoring with nothing hooked is the normal case and must not invent a color."""
    from bartleby.qt.theming.account_accent import account_accent  # noqa: PLC0415

    dcs = DuelCombatStrategy(name="Boss", player_assignments=[PlayerAssignment(players=[1])])
    save(dcs, roots[0])

    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id)

    card = dlg._pages[StrategyKind.DUEL]._editor.cards[0]
    assert account_accent(4) not in card.pills.pills[0].styleSheet()


def test_an_assignment_added_later_gets_the_colors_too(qapp, roots):
    """The keys are held, not just applied: cards are built long after the dialog opens."""
    from bartleby.qt.theming.account_accent import account_accent  # noqa: PLC0415

    dcs = DuelCombatStrategy(name="Boss")
    save(dcs, roots[0])
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id, player_color_keys=[4, 5, 6, 7])
    editor = dlg._pages[StrategyKind.DUEL]._editor

    editor._on_add_assignment()

    assert account_accent(4) in editor.cards[0].pills.pills[0].styleSheet()


def test_the_colors_reach_the_combat_order_chips_as_well(qapp, roots):
    from bartleby.qt.theming.account_accent import account_accent  # noqa: PLC0415

    dcs = DuelCombatStrategy(name="Boss", combat_order=[1, 2, 3, 4])
    save(dcs, roots[0])

    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=dcs.id, player_color_keys=[4, 5, 6, 7])

    chips = dlg._pages[StrategyKind.DUEL]._editor._order_widget._chips
    assert account_accent(4) in next(c for c in chips if c._player_num == 1).styleSheet()


# ---------------------------------------------------------------------------
# Core's own window: what every product gets, and nothing of one product's
# ---------------------------------------------------------------------------


def test_the_library_shows_every_tab_and_a_pick_only_its_own():
    assert tabs_for(DialogMode.LIBRARY, StrategyKind.DUEL) == tuple(KIND_LABELS)
    for kind in StrategyKind:
        assert tabs_for(DialogMode.PICK, kind) == (kind,)


def test_building_the_window_never_shows_a_parentless_widget(qapp, roots, parentless_shows):
    entry = _one_of_each(roots)[StrategyKind.DUEL]

    _dialog(roots, kind=StrategyKind.DUEL, current_id=entry.id)

    assert parentless_shows == []


def test_a_card_has_no_menu_until_a_product_adds_one(qapp, roots):
    entry = _one_of_each(roots)[StrategyKind.DUEL]
    dlg = _dialog(roots, kind=StrategyKind.DUEL, current_id=entry.id)
    page = dlg.page(StrategyKind.DUEL)

    assert page.card_menu(entry.id) is None
    item, _card = _row(page, entry.id)
    page._show_card_menu(page._list.visualItemRect(item).center())  # nothing opens


def test_a_window_with_no_deck_link_offers_no_client_verbs(qapp, roots):
    entry = _one_of_each(roots)[StrategyKind.DECK]
    dlg = _dialog(roots, kind=StrategyKind.DECK, current_id=entry.id)
    editor = dlg.page(StrategyKind.DECK).editor

    assert editor.import_button.isHidden()
    assert editor.apply_button.isHidden()


def test_a_window_with_a_deck_link_offers_them(qapp, roots):
    entry = _one_of_each(roots)[StrategyKind.DECK]
    link = DeckClientLink(lambda: 1, lambda _slot, _done: False, lambda _slot, _deck, _done: False)
    dlg = _dialog(roots, kind=StrategyKind.DECK, current_id=entry.id, deck_link=link)
    editor = dlg.page(StrategyKind.DECK).editor

    assert not editor.import_button.isHidden()
    assert not editor.apply_button.isHidden()


def test_a_product_with_no_archmastery_setting_has_no_caption_for_one(qapp, roots):
    deck = DeckPreset(name="Storm 60", deck=DeckData(normal={"Test Card": 1}, archmastery_school="Storm"))
    _dlg, page = _deck_page(roots, deck)
    assert page.editor.archmastery.caption_text == ""

    asked = _dialog(roots, kind=StrategyKind.DECK, current_id=deck.id, archmastery_default=lambda: True)
    assert asked.page(StrategyKind.DECK).editor.archmastery.caption_text == APP_SETTING_ON


def test_a_revert_and_a_newer_file_name_the_running_product(qapp, roots):
    shipped, user = roots
    ours = PlayerCombatStrategy(name="Support", dsl="pass")
    newer = PlayerCombatStrategy(name="Later", dsl="pass", format=99)
    save(ours, shipped)
    save(ours.model_copy(update={"name": "Mine"}), user)
    save(newer, user)
    dlg = _dialog(roots, kind=StrategyKind.PLAYER, current_id=ours.id)
    page = dlg.page(StrategyKind.PLAYER)
    assert "a strategy Sample ships" in page.about.origin_text

    page.select(newer.id)

    assert page.about.origin_text == "Saved by a newer version. Update Sample to edit it."
    assert not page.delete_button.isEnabled()
    assert not page.duplicate_button.isEnabled()


# ---------------------------------------------------------------------------
# An owner's entries: the page knows them from the library's, and draws none of its own accord
# ---------------------------------------------------------------------------


def _owned(roots) -> tuple[StrategyWorkingSet, DuelCombatStrategy]:
    duel = DuelCombatStrategy(name="First Boss")
    ws = StrategyWorkingSet.from_store(roots, owner=FakeOwner("Solo", duels=[duel]))
    return ws, duel


def test_an_owners_entries_are_not_listed_by_core(qapp, roots):
    ws, duel = _owned(roots)
    page = KindPage(StrategyKind.DUEL, ws, SampleStrategySettings())

    assert page.card_for(duel.id) is None
    assert not page.creates_own
    page.new_button.click()
    assert not ws.is_own_entry(StrategyKind.DUEL, page.selected_id())


class _OwnPage(KindPage):
    """A page that draws its owner's entries, as a product's subclass would."""

    own_source = "Mine alone"

    def _filter_source(self):
        return self.own_source

    @property
    def creates_own(self):
        return True

    def _favorable(self, section):
        return section != "own"

    def _sections(self):
        own = self._ws.own_items(self._kind)
        return [("own", self._ws.owner.name, own), *self._library_sections({e.id for e in own}, "Library")]

    def _on_empty_section(self, key):
        if key == "own":
            self._add_section_note(key, QLabel("Nothing here yet"))


def test_a_subclass_draws_an_owners_section_first_with_its_word_in_the_filter(qapp, roots):
    save(DuelCombatStrategy(name="Library"), roots[0])
    ws, duel = _owned(roots)
    page = _OwnPage(StrategyKind.DUEL, ws, SampleStrategySettings())

    assert page.section_headings() == ["Solo", "Library"]
    assert page.ids_in_section("own") == [duel.id]
    assert page.card_for(duel.id).star is None, "favorites are the library's"

    pick(page.filter_bar.tag_pickers["Source"], "Mine alone")

    assert page.visible_ids() == [duel.id]


def test_a_subclass_creates_in_the_owner_and_says_where_an_entry_lives(qapp, roots):
    ws, _duel = _owned(roots)
    page = _OwnPage(StrategyKind.DUEL, ws, SampleStrategySettings())

    page.new_button.click()

    assert ws.is_own_entry(StrategyKind.DUEL, page.selected_id())
    assert page.about.origin_text == "In 'Solo' only"
    assert page.delete_button.text() == "Delete" and page.delete_button.isEnabled()


def test_an_empty_section_keeps_its_heading_over_the_note_a_subclass_adds(qapp, roots):
    ws = StrategyWorkingSet.from_store(roots, owner=FakeOwner("Solo"))
    page = _OwnPage(StrategyKind.DUEL, ws, SampleStrategySettings())

    page.filter_bar.search.setText("matches nothing")

    assert page.section_headings() == ["Solo"]
