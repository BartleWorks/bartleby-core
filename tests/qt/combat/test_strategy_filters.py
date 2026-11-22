"""Tags, filters and favorites in the combat strategy dialog (#125), as widgets.

The rules themselves are tested Qt-free in ``test_strategy_tags.py``. What is tested
here is what only the dialog knows: which pickers each tab offers, that a facet hides
cards without rebuilding them, that a chip undoes its filter, that a star is saved on
the click and moves its card into a section of its own, and that roles edited in the
About section reach the entry and its card.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QCoreApplication, QEvent, QPointF
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtWidgets import QApplication

from bartleby.qt.combat.dialog import FAVORITES_HEADING, CombatStrategyDialog, others_heading
from bartleby.qt.testing.picker_helpers import pick
from bartleby.qt.widgets.cards.strategy_card import StrategyCard
from bartleby.qt.widgets.pills.pill import NeutralChip
from bartleby.qt.widgets.pills.tag_pill import TagChip
from bartleby.runtime.combat.strategy_summary import StrategySummary
from bartleby.runtime.combat.strategy_workset import StrategyWorkingSet
from bartleby.shared.store_files import SOURCE_LABELS, Origin
from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import DeckPreset, DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy
from tests.qt.combat.library import SampleStrategySettings, library_roots, open_dialog, save

BUILT_IN = SOURCE_LABELS[Origin.SHIPPED]
CUSTOM = SOURCE_LABELS[Origin.USER]


@pytest.fixture
def roots(tmp_path):
    return library_roots(tmp_path)


@pytest.fixture
def settings():
    """The product's settings every window of the test keeps its favorites in."""
    return SampleStrategySettings()


_dialog = open_dialog


def _flush_deletes() -> None:
    """The suite runs no event loop, so a deleteLater has to be delivered by hand."""
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    QApplication.processEvents()


def _players(roots):
    """A built-in Support, a built-in Hitter for Storm or Fire, and a custom Healer."""
    shipped, user = roots
    support = PlayerCombatStrategy(name="Support", dsl="x", tags=["Support"])
    hitter = PlayerCombatStrategy(name="Hitter", dsl="x", tags=["Hitter"], schools=["Storm", "Fire"])
    healer = PlayerCombatStrategy(name="Healer", dsl="x", tags=["Healer"], schools=["Life"])
    save(support, shipped)
    save(hitter, shipped)
    save(healer, user)
    return support, hitter, healer


def _player_page(roots, settings=None):
    """The dialog, its working set, and its Player tab. Hold the dialog: the page dies with it."""
    ws = StrategyWorkingSet.from_store(roots)
    dlg = CombatStrategyDialog(ws, settings or SampleStrategySettings(), kind=StrategyKind.PLAYER)
    return dlg, ws, dlg._pages[StrategyKind.PLAYER]


# ---------------------------------------------------------------------------
# Which pickers each tab offers
# ---------------------------------------------------------------------------


def test_the_duel_tab_offers_source_school_and_team_size_but_no_role(qapp, roots):
    bar = _dialog(roots)._pages[StrategyKind.DUEL].filter_bar
    assert list(bar.tag_pickers) == ["Source"]
    assert bar.school_picker is not None
    assert bar.team_picker is not None


def test_the_player_tab_offers_source_role_and_school(qapp, roots):
    bar = _dialog(roots)._pages[StrategyKind.PLAYER].filter_bar
    assert list(bar.tag_pickers) == ["Source", "Role"]
    assert bar.school_picker is not None
    assert bar.team_picker is None


def test_the_deck_tab_offers_source_role_and_school(qapp, roots):
    """A deck's school joined the filter in #174."""
    bar = _dialog(roots)._pages[StrategyKind.DECK].filter_bar
    assert list(bar.tag_pickers) == ["Source", "Role"]
    assert bar.school_picker is not None
    assert bar.team_picker is None


def test_works_with_school_on_the_deck_tab_includes_decks_for_any_school(qapp, roots):
    shipped, user = roots
    storm = DeckPreset(name="Storm Deck", schools=["Storm"])
    ice = DeckPreset(name="Ice Deck", schools=["Ice"])
    anyone = DeckPreset(name="Anyone's Deck")
    for preset in (storm, ice, anyone):
        save(preset, user)
    dlg = _dialog(roots, kind=StrategyKind.DECK)
    page = dlg._pages[StrategyKind.DECK]

    pick(page.filter_bar.school_picker, "Storm")

    assert set(page.visible_ids()) == {storm.id, anyone.id}


def test_a_decks_schools_are_picked_in_its_editor_and_shown_on_its_card(qapp, roots):
    shipped, user = roots
    preset = DeckPreset(name="Deck")
    save(preset, user)
    dlg = _dialog(roots, kind=StrategyKind.DECK, current_id=preset.id)
    page = dlg._pages[StrategyKind.DECK]

    pick(page.editor.school_picker, "Ice")

    assert dlg._ws.get(StrategyKind.DECK, preset.id).schools == ["Ice"]
    assert any("Ice" in line for line in page.card_for(preset.id).sub_texts)


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def test_a_role_filter_hides_rows_without_rebuilding_them(qapp, roots):
    support, hitter, healer = _players(roots)
    _dlg, _ws, page = _player_page(roots)
    before = list(page._card_map)

    pick(page.filter_bar.tag_pickers["Role"], "Hitter", "Healer")

    assert set(page.visible_ids()) == {hitter.id, healer.id}
    assert page._card_map == before


def test_role_and_source_and_across_groups(qapp, roots):
    support, hitter, healer = _players(roots)
    _dlg, _ws, page = _player_page(roots)

    pick(page.filter_bar.tag_pickers["Role"], "Hitter", "Healer")
    pick(page.filter_bar.tag_pickers["Source"], CUSTOM)

    assert page.visible_ids() == [healer.id]


def test_works_with_school_includes_strategies_for_any_school(qapp, roots):
    support, hitter, healer = _players(roots)
    _dlg, _ws, page = _player_page(roots)

    pick(page.filter_bar.school_picker, "Storm")

    # Support names no school, so it works with Storm too; the Healer is Life only.
    assert set(page.visible_ids()) == {support.id, hitter.id}


def test_the_source_filter_follows_an_unsaved_edit_on_the_next_filter_change(qapp, roots):
    support, _hitter, _healer = _players(roots)
    _dlg, _ws, page = _player_page(roots)
    page.select(support.id)
    page.about._description_edit.setText("edited")

    pick(page.filter_bar.tag_pickers["Source"], SOURCE_LABELS[Origin.SHIPPED_OVERRIDE])

    assert page.visible_ids() == [support.id]


def test_team_size_filters_duels_by_their_player_range(qapp, roots):
    ws = StrategyWorkingSet.from_store(roots)
    solo = ws.create(StrategyKind.DUEL, "Solo")
    solo.min_players, solo.max_players = 1, 1
    team = ws.create(StrategyKind.DUEL, "Team")
    team.min_players, team.max_players = 2, 4
    dlg = CombatStrategyDialog(ws, SampleStrategySettings())
    page = dlg._pages[StrategyKind.DUEL]
    page.rebuild_list()

    pick(page.filter_bar.team_picker, "3")

    assert page.visible_ids() == [team.id]


def test_works_with_school_on_a_duel_asks_every_slot(qapp, roots):
    ws = StrategyWorkingSet.from_store(roots)
    storm_only = ws.create(StrategyKind.DUEL, "Storm only")
    storm_only.max_players = 1
    storm_only.player_assignments = [PlayerAssignment(players=[1], dsl="x", schools=["Storm"])]
    open_slot = ws.create(StrategyKind.DUEL, "P2 is anyone")
    open_slot.max_players = 2
    open_slot.player_assignments = [PlayerAssignment(players=[1], dsl="x", schools=["Storm"])]
    dlg = CombatStrategyDialog(ws, SampleStrategySettings())
    page = dlg._pages[StrategyKind.DUEL]
    page.rebuild_list()

    pick(page.filter_bar.school_picker, "Life")

    assert page.visible_ids() == [open_slot.id]


def test_the_query_and_the_facets_intersect(qapp, roots):
    support, hitter, healer = _players(roots)
    _dlg, _ws, page = _player_page(roots)

    pick(page.filter_bar.tag_pickers["Source"], BUILT_IN)
    page.filter_bar.search.setText("hit")

    assert page.visible_ids() == [hitter.id]


def test_a_role_is_searchable_as_text(qapp, roots):
    support, _hitter, _healer = _players(roots)
    _dlg, _ws, page = _player_page(roots)

    page.filter_bar.search.setText("support")

    assert page.visible_ids() == [support.id]


def test_pickers_count_what_each_value_would_show(qapp, roots):
    _players(roots)
    _dlg, _ws, page = _player_page(roots)
    [(_name, rows)] = page.filter_bar.tag_pickers["Source"]._sections

    assert {r.key: r.meta for r in rows} == {
        BUILT_IN: "2 strategies",
        SOURCE_LABELS[Origin.SHIPPED_OVERRIDE]: "0 strategies",
        CUSTOM: "1 strategy",
    }


# ---------------------------------------------------------------------------
# Chips
# ---------------------------------------------------------------------------


def test_each_selected_value_shows_a_chip(qapp, roots):
    _players(roots)
    _dlg, _ws, page = _player_page(roots)
    bar = page.filter_bar

    pick(bar.tag_pickers["Role"], "Healer")
    pick(bar.school_picker, "Ice")

    assert bar.chip_texts() == ["Healer", "Ice"]
    assert not bar._chip_bar.isHidden()


def test_removing_a_chip_drops_its_filter(qapp, roots):
    support, hitter, healer = _players(roots)
    _dlg, _ws, page = _player_page(roots)
    bar = page.filter_bar
    pick(bar.tag_pickers["Role"], "Healer")

    [chip] = bar._chip_bar.findChildren(TagChip)
    chip.click()
    _flush_deletes()

    assert bar.selection().is_empty
    assert set(page.visible_ids()) == {support.id, hitter.id, healer.id}
    assert bar._chip_bar.isHidden()


def test_removing_a_school_chip_drops_that_school_only(qapp, roots):
    _players(roots)
    _dlg, _ws, page = _player_page(roots)
    bar = page.filter_bar
    pick(bar.school_picker, "Ice", "Fire")

    chip = next(c for c in bar._chip_bar.findChildren(NeutralChip) if c.value == "Ice")
    chip.click()
    _flush_deletes()

    assert bar.selection().schools == {"Fire"}
    assert bar.school_picker.value() == ["Fire"]


def test_the_pencil_clears_facets_that_would_hide_the_strategy(qapp, roots):
    support, hitter, healer = _players(roots)
    _dlg, _ws, page = _player_page(roots)
    pick(page.filter_bar.tag_pickers["Role"], "Healer")

    page.clear_filter()

    assert page.filter_bar.selection().is_empty
    assert len(page.visible_ids()) == 3


def test_filters_do_not_persist_between_openings(qapp, roots):
    _players(roots)
    _dlg, _ws, page = _player_page(roots)
    pick(page.filter_bar.tag_pickers["Role"], "Healer")

    _dlg2, _ws2, page2 = _player_page(roots)

    assert page2.filter_bar.selection().is_empty
    assert len(page2.visible_ids()) == 3


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------


def test_no_headers_until_something_is_a_favorite(qapp, roots, settings):
    _players(roots)
    _dlg, _ws, page = _player_page(roots, settings)
    assert page.section_headings() == []


def test_a_favorite_is_listed_first_under_its_own_heading(qapp, roots, settings):
    support, hitter, healer = _players(roots)
    settings.toggle_favorite_strategy(StrategyKind.PLAYER.value, healer.id)

    _dlg, _ws, page = _player_page(roots, settings)

    assert page.section_headings() == [FAVORITES_HEADING, others_heading(StrategyKind.PLAYER)]
    assert page.visible_ids()[0] == healer.id


def test_every_entry_appears_exactly_once(qapp, roots, settings):
    """Selection is restored by id, so an entry drawn twice would make it ambiguous."""
    support, hitter, healer = _players(roots)
    settings.toggle_favorite_strategy(StrategyKind.PLAYER.value, healer.id)

    _dlg, _ws, page = _player_page(roots, settings)

    assert sorted(page.visible_ids()) == sorted([support.id, hitter.id, healer.id])


def test_clicking_the_star_saves_at_once_and_moves_the_card(qapp, roots, settings):
    support, hitter, healer = _players(roots)
    _dlg, ws, page = _player_page(roots, settings)
    page.select(hitter.id)

    page.card_for(hitter.id).star.click()
    _flush_deletes()

    # Told on the click, with no Save pressed: the product's settings save at once.
    assert settings.is_favorite_strategy(StrategyKind.PLAYER.value, hitter.id)
    assert page.visible_ids()[0] == hitter.id
    assert page.section_headings()[0] == FAVORITES_HEADING
    assert page.selected_id() == hitter.id
    assert not ws.is_dirty  # a preference, not a change to the library


def test_cancel_does_not_take_a_favorite_back(qapp, roots, settings):
    support, _hitter, _healer = _players(roots)
    dlg = CombatStrategyDialog(StrategyWorkingSet.from_store(roots), settings, kind=StrategyKind.PLAYER)

    dlg._pages[StrategyKind.PLAYER].set_favorite(support.id, True)
    dlg.reject()

    assert settings.is_favorite_strategy(StrategyKind.PLAYER.value, support.id)


def test_unstarring_the_last_favorite_removes_the_headers(qapp, roots, settings):
    support, _hitter, _healer = _players(roots)
    settings.toggle_favorite_strategy(StrategyKind.PLAYER.value, support.id)
    _dlg, _ws, page = _player_page(roots, settings)

    page.card_for(support.id).star.click()
    _flush_deletes()

    assert page.section_headings() == []
    assert not page.is_favorite(support.id)


def test_a_section_the_filter_empties_hides_its_heading(qapp, roots, settings):
    support, hitter, healer = _players(roots)
    settings.toggle_favorite_strategy(StrategyKind.PLAYER.value, healer.id)
    _dlg, _ws, page = _player_page(roots, settings)

    pick(page.filter_bar.tag_pickers["Role"], "Hitter")

    assert page.section_headings() == [others_heading(StrategyKind.PLAYER)]


# ---------------------------------------------------------------------------
# The card's star
# ---------------------------------------------------------------------------


def _card(favorite):
    return StrategyCard(StrategySummary(name="A", description=""), favorite=favorite)


def test_a_card_without_favorites_has_no_star(qapp):
    assert _card(None).star is None


def test_a_favorites_star_always_shows(qapp):
    assert not _card(True).star.isHidden()


def test_an_outline_star_shows_only_on_hover_or_selection(qapp):
    card = _card(False)
    assert card.star.isHidden()

    card.set_selected(True)
    assert not card.star.isHidden()

    card.set_selected(False)
    card.enterEvent(QEnterEvent(QPointF(1, 1), QPointF(1, 1), QPointF(1, 1)))
    assert not card.star.isHidden()
    card.leaveEvent(QEvent(QEvent.Type.Leave))
    assert card.star.isHidden()


def test_the_star_reports_what_it_now_wants(qapp):
    card = _card(False)
    seen = []
    card.favorite_toggled.connect(seen.append)
    card.set_selected(True)

    card.star.click()

    assert seen == [True]


# ---------------------------------------------------------------------------
# Roles in the About section, and on the card
# ---------------------------------------------------------------------------


def test_the_duel_tab_offers_no_role_editor(qapp, roots):
    assert _dialog(roots)._pages[StrategyKind.DUEL].about.tag_pickers == []


def test_picking_a_role_writes_it_to_the_entry_and_its_card(qapp, roots):
    support, _hitter, _healer = _players(roots)
    _dlg, ws, page = _player_page(roots)
    page.select(support.id)
    [picker] = page.about.tag_pickers

    pick(picker, "Healer")

    entry = ws.get(StrategyKind.PLAYER, support.id)
    assert entry.tags == ["Support", "Healer"]
    assert page.card_for(support.id).tag_texts == ["Support", "Healer"]


def test_loading_an_entry_shows_its_roles_without_editing_it(qapp, roots):
    _support, hitter, _healer = _players(roots)
    _dlg, ws, page = _player_page(roots)

    page.select(hitter.id)

    assert page.about.tag_pickers[0].tags() == ["Hitter"]
    assert page.about.tag_pickers[0].text() == "Hitter"  # names the value, not "Role (1)"
    assert not ws.is_dirty


def test_a_deck_preset_role_round_trips_through_save(qapp, roots):
    ws = StrategyWorkingSet.from_store(roots)
    preset = ws.create(StrategyKind.DECK, "Deck")
    dlg = CombatStrategyDialog(ws, SampleStrategySettings(), kind=StrategyKind.DECK, current_id=preset.id)
    page = dlg._pages[StrategyKind.DECK]
    page.select(preset.id)

    pick(page.about.tag_pickers[0], "Support")
    ws.commit()

    [saved] = StrategyWorkingSet.from_store(roots).items(StrategyKind.DECK)
    assert isinstance(saved, DeckPreset)
    assert saved.tags == ["Support"]


def test_a_duel_card_shows_no_role_pills(qapp, roots):
    ws = StrategyWorkingSet.from_store(roots)
    duel = ws.create(StrategyKind.DUEL, "Duel")
    dlg = CombatStrategyDialog(ws, SampleStrategySettings())
    page = dlg._pages[StrategyKind.DUEL]
    page.rebuild_list()

    assert isinstance(duel, DuelCombatStrategy)
    assert page.card_for(duel.id).tag_texts == []
