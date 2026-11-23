"""A deck's name on the game's wheels, and the decks the user's wizards own behind the button."""

from __future__ import annotations

import pytest

from bartleby.qt.widgets.pickers.deck_name_picker import NO_OWNED_TIP, OWNED_TIP, DeckNamePicker, owned_names
from bartleby.runtime.views import DeckWizard
from bartleby.shared.deck_names import DeckName, Wheel

MAIN, BOSS = DeckName(type_key="Type_1"), DeckName(type_key="Type_2")
STORM_MAIN_2 = DeckName(class_key="Class_7", type_key="Type_1", num_key="Num_2")
HEALER, OTHER_HEALER = DeckName(type_key="Type_12"), DeckName(type_key="Type_18")

P1 = DeckWizard(101, slot=1, decks=(MAIN, BOSS))
P2 = DeckWizard(102, slot=2, decks=(BOSS, OTHER_HEALER))


def _pick(picker: DeckNamePicker, wheel: Wheel, key: str) -> None:
    combo = picker.wheels[wheel]
    combo.setCurrentIndex(combo.findData(key))


def _entries(picker: DeckNamePicker) -> dict:
    return {action.text(): action for action in picker.owned_menu().actions()}


def test_empty_wheels_name_no_deck_and_say_what_that_means(qapp):
    picker = DeckNamePicker(none_text="Keeps its deck")

    assert picker.name is None
    assert picker.preview_text == "Keeps its deck"


@pytest.mark.parametrize("name", [BOSS, STORM_MAIN_2, OTHER_HEALER])
def test_a_name_goes_on_the_wheels_and_comes_back_off_them(qapp, name):
    picker = DeckNamePicker(name)

    assert picker.name == name
    assert picker.preview_text == name.text()


def test_the_wheels_make_a_name_as_the_game_reads_it(qapp):
    picker = DeckNamePicker()
    heard = []
    picker.changed.connect(heard.append)

    _pick(picker, Wheel.TYPE, "Type_1")
    _pick(picker, Wheel.CLASS, "Class_7")
    _pick(picker, Wheel.NUM, "Num_2")

    assert picker.name == STORM_MAIN_2 and picker.preview_text == "Storm Main Deck 2"
    assert heard[-1] == STORM_MAIN_2 and len(heard) == 3


def test_two_choices_that_read_alike_are_two_entries(qapp):
    picker = DeckNamePicker()
    wheel = picker.wheels[Wheel.TYPE]
    labels = [wheel.itemText(i) for i in range(wheel.count())]

    assert "Healer" in labels and "Healer (2)" in labels


def test_a_key_newer_than_the_snapshot_is_kept(qapp):
    newer = DeckName(type_key="Type_400")
    picker = DeckNamePicker(newer)

    assert picker.name == newer
    picker.set_name(None)
    assert picker.name is None


def test_the_button_is_gray_until_a_wizard_s_decks_are_known(qapp):
    picker = DeckNamePicker()
    assert not picker.owned_button.isEnabled() and picker.owned_button.toolTip() == NO_OWNED_TIP

    picker.set_wizards([DeckWizard(101, slot=1)])
    assert not picker.owned_button.isEnabled(), "a wizard whose decks weren't read offers none"

    picker.set_wizards([P1])
    assert picker.owned_button.isEnabled() and picker.owned_button.toolTip() == OWNED_TIP


def test_the_decks_owned_are_listed_by_how_many_own_them():
    assert owned_names([P1, P2]) == [(BOSS, [1, 2]), (OTHER_HEALER, [2]), (MAIN, [1])]


def test_a_deck_picked_from_the_list_goes_on_the_wheels_key_for_key(qapp):
    picker = DeckNamePicker()
    picker.set_wizards([P1, P2])
    heard = []
    picker.changed.connect(heard.append)

    entries = _entries(picker)
    assert list(entries) == ["Boss Deck\tP1 P2", "Healer Deck (Healer (2))\tP2", "Main Deck\tP1"]

    entries["Healer Deck (Healer (2))\tP2"].trigger()
    assert picker.name == OTHER_HEALER != HEALER
    assert heard == [OTHER_HEALER]
