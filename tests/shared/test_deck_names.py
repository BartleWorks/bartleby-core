"""Deck names by key, and the checked-in snapshot of the game's DeckNames.lang.

The snapshot under test is the real one: it is what a game update can make stale.
"""

import json
import re

import pytest
from pydantic import BaseModel, ValidationError

from bartleby.shared import deck_names
from bartleby.shared.deck_names import DeckName, Wheel, choices, locales, matches, same_text
from bartleby.shared.locales import LOCALES

SNAPSHOT: dict[str, dict[str, str]] = json.loads(deck_names.DECK_NAMES_PATH.read_text(encoding="utf-8"))["locales"]

BOSS = DeckName(type_key="Type_2")
STORM_MAIN_2 = DeckName(class_key="Class_7", type_key="Type_1", num_key="Num_2")
AHMARRA = DeckName(class_key="Class_36")
HEALER, OTHER_HEALER = DeckName(type_key="Type_12"), DeckName(type_key="Type_18")


# -- a name, by its keys -----------------------------------------------------------------


def test_a_name_is_the_wheels_memory_holds():
    name = DeckName.from_indices(3, 37, 1)

    assert name.keys == ("Class_3", "Type_37", "Num_1")
    assert name.indices == (3, 37, 1)


def test_a_wheel_left_empty_is_no_key():
    name = DeckName.from_indices(0, 2, 0)

    assert name == BOSS
    assert name.keys == ("", "Type_2", "")
    assert name.indices == (0, 2, 0)


def test_every_spelling_of_a_choice_is_one_name():
    assert DeckName(class_key="Class_0", type_key="Type_02") == BOSS


def test_a_deck_never_named_has_no_name():
    assert DeckName.from_indices(0, 0, 0) is None
    with pytest.raises(ValidationError):
        DeckName()
    with pytest.raises(ValidationError):
        DeckName(class_key="Class_0", type_key="Type_0", num_key="Num_0")


@pytest.mark.parametrize("key", ["Storm", "Type_2", "Class_", "Class_x", "Class_-1", "Class_2 "])
def test_a_key_of_another_wheel_or_of_none_is_refused(key):
    with pytest.raises(ValidationError):
        DeckName(class_key=key)


class _Holder(BaseModel):
    """What a file holding a name is: Stage 9's deck set is one."""

    deck: DeckName | None = None


def test_a_name_round_trips_through_a_file_with_its_empty_wheels_left_out():
    body = _Holder(deck=BOSS).model_dump(exclude_defaults=True)

    assert body == {"deck": {"type_key": "Type_2"}}
    assert _Holder.model_validate(json.loads(json.dumps(body))).deck == BOSS
    assert _Holder().model_dump(exclude_defaults=True) == {}


def test_a_key_newer_than_the_snapshot_is_kept():
    name = DeckName(class_key="Class_999")

    assert DeckName.model_validate(name.model_dump(exclude_defaults=True)) == name
    assert name.text() == "Class_999Deck"


def test_a_name_is_hashable():
    assert {BOSS: 1}[DeckName.from_indices(0, 2, 0)] == 1


# -- its text ------------------------------------------------------------------------------


def test_a_name_reads_as_the_backpack_showed_it():
    """Each as four live clients' backpacks showed it on 2026-11-21."""
    assert DeckName.from_indices(3, 37, 1).text() == "Fire Vanguard Deck 1"
    assert STORM_MAIN_2.text() == "Storm Main Deck 2"
    assert BOSS.text() == "Boss Deck"


def test_the_snapshot_holds_every_locale():
    assert locales() == list(LOCALES)


@pytest.mark.parametrize("locale", LOCALES)
def test_every_name_renders_in_every_locale_the_snapshot_holds(locale):
    """Each choice of each wheel, alone and all three together, with no slot, key or markup left in it."""
    keys = {wheel: [key for key in SNAPSHOT[locale] if key.startswith(f"{wheel}_")] for wheel in Wheel}
    names = [DeckName(**{f"{wheel.lower()}_key": key}) for wheel in Wheel for key in keys[wheel]]
    names += [
        DeckName(class_key=class_, type_key=type_, num_key="Num_7")
        for class_ in keys[Wheel.CLASS]
        for type_ in keys[Wheel.TYPE]
    ]
    for name in names:
        text = name.text(locale)
        assert text == text.strip() and text
        assert not re.search(r"\$|[A-Z][a-z]+_\d|<[^<>]*>|\{|  ", text), (name, text)


def test_a_part_the_locale_lacks_is_shown_in_english():
    assert "Class_36" in SNAPSHOT["en-US"] and "Class_36" not in SNAPSHOT["de"]
    assert "Ahmarra" in AHMARRA.text("de")


def test_a_conjugated_verb_reads_in_its_plain_form():
    """de's Practice is "#-1:$PLURALITY$#-1:{Übe[1]|Übt[p]|Übe}" in the game's file."""
    assert "$PLURALITY$" in SNAPSHOT["de"]["Type_34"]
    assert DeckName(type_key="Type_34").text("de") == "ÜbeKartenspiel"


def test_a_part_that_is_not_markup_stays():
    """de's Slow is "<<<<<" in the game's file."""
    assert DeckName(type_key="Type_33").text("de") == "<<<<<Kartenspiel"


# -- reading a row ---------------------------------------------------------------------------


@pytest.mark.parametrize("locale", LOCALES)
def test_a_row_matches_in_any_locale(locale):
    assert matches(STORM_MAIN_2, STORM_MAIN_2.text(locale))
    assert not matches(BOSS, STORM_MAIN_2.text(locale))


def test_a_row_matches_whatever_its_spacing_case_and_markup():
    assert matches(BOSS, "<center>Boss  Deck </center>")
    assert matches(BOSS, "boss deck")
    # es composes an empty wheel as a doubled space, and what the game makes of it is not known.
    assert matches(BOSS, "Baraja  Jefe de los monstruos ")


def test_an_empty_row_matches_nothing():
    assert not matches(BOSS, "")
    assert not matches(BOSS, "<center></center>")


def test_a_locale_lacking_a_part_offers_no_text_to_match():
    """de would show Class_36 some other way, so en-US's word in German's order proves nothing."""
    assert not matches(AHMARRA, AHMARRA.text("de"))
    assert matches(AHMARRA, AHMARRA.text("en-US"))


def test_two_names_can_share_their_text():
    assert HEALER != OTHER_HEALER
    assert HEALER.text() == OTHER_HEALER.text() == "Healer Deck"
    assert same_text(HEALER, OTHER_HEALER)
    assert same_text(DeckName(type_key="Type_13", num_key="Num_1"), DeckName(type_key="Type_21", num_key="Num_1"))
    assert not same_text(HEALER, BOSS)


# -- a picker's choices ----------------------------------------------------------------------


def test_a_wheel_offers_its_empty_choice_and_then_the_games_order():
    offered = choices(Wheel.NUM)

    assert [c.key for c in offered] == ["", *(f"Num_{n}" for n in range(1, 8))]
    assert [c.label for c in offered] == ["(None)", "1", "2", "3", "4", "5", "6", "7"]
    # By number, not by text: Type_10 comes after Type_9.
    assert [Wheel.TYPE.index(c.key) for c in choices(Wheel.TYPE)] == list(range(45))


def test_the_choices_are_the_locales_own():
    assert "Class_36" in [c.key for c in choices(Wheel.CLASS)]
    assert "Class_36" not in [c.key for c in choices(Wheel.CLASS, "de")]
    assert choices(Wheel.CLASS, "de")[0].label == "(nichts)"


@pytest.mark.parametrize("locale", LOCALES)
def test_every_choice_of_a_wheel_can_be_told_apart(locale):
    for wheel in Wheel:
        labels = [c.label for c in choices(wheel, locale)]
        assert all(labels)
        assert len(labels) == len(set(labels))


def test_two_choices_of_one_text_are_numbered():
    by_key = {c.key: c for c in choices(Wheel.TYPE)}

    assert by_key["Type_12"].text == by_key["Type_18"].text == "Healer"
    assert (by_key["Type_12"].label, by_key["Type_18"].label) == ("Healer", "Healer (2)")


def test_a_missing_snapshot_names_nothing_rather_than_raising(monkeypatch, tmp_path):
    monkeypatch.setattr(deck_names, "DECK_NAMES_PATH", tmp_path / "gone.json")
    deck_names._snapshot.cache_clear()
    deck_names._folded_texts.cache_clear()
    try:
        assert locales() == []
        assert not matches(BOSS, "Boss Deck")
        assert BOSS.text() == "Type_2"
    finally:
        deck_names._snapshot.cache_clear()
        deck_names._folded_texts.cache_clear()
