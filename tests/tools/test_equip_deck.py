"""The pure parts of ``tools/equip_deck.py``: what a target names, and the deck a name says."""

import argparse

import pytest

from bartleby.runtime.decks import OwnedDeck
from bartleby.shared.deck_names import DeckName
from tools import equip_deck as tool

BOSS = DeckName(type_key="Type_2")
HEALER, OTHER_HEALER = DeckName(type_key="Type_12"), DeckName(type_key="Type_18")


def _owned(*names: DeckName | None) -> list[OwnedDeck]:
    return [OwnedDeck(global_id=i, name=name, equipped=False, cards=1) for i, name in enumerate(names)]


def test_a_target_is_a_client_and_a_name():
    assert tool.target("c1=Boss Deck") == ("C1", "Boss Deck")
    for text in ("C1", "C1=", "C1= "):
        with pytest.raises(argparse.ArgumentTypeError):
            tool.target(text)


def test_a_name_is_found_among_the_decks_owned_in_any_locale():
    owned = _owned(None, BOSS, HEALER)

    assert tool.find_name("Boss Deck", owned) == BOSS
    assert tool.find_name(BOSS.text("pl"), owned) == BOSS


def test_a_name_is_found_by_its_keys_whoever_owns_it():
    assert tool.find_name("Class_7+Type_1+Num_2", []) == DeckName.from_indices(7, 1, 2)
    assert tool.find_name("Type_2", []) == BOSS


def test_a_name_no_one_owns_or_two_that_read_alike_are_asked_for_by_key():
    with pytest.raises(ValueError, match="owns"):
        tool.find_name("Boss Deck", _owned(HEALER))
    with pytest.raises(ValueError, match="Type_12.*Type_18|Type_18.*Type_12"):
        tool.find_name("Healer Deck", _owned(HEALER, OTHER_HEALER))


def test_a_deck_s_line_says_its_keys_and_cards_and_whether_it_is_on():
    line = tool.deck_line(OwnedDeck(global_id=1, name=BOSS, equipped=True, cards=12))

    assert "Boss Deck" in line and "Type_2" in line and "12 cards" in line and "EQUIPPED" in line
    assert "(never named)" in tool.deck_line(OwnedDeck(global_id=1, name=None, equipped=False, cards=0))
