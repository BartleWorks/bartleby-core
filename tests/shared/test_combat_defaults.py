"""A default duel's label in the log, a choice's notes, and the DSL each player slot of a duel runs."""

from __future__ import annotations

import pytest

from bartleby.shared.combat_defaults import (
    LAST_RESORT_DSL,
    DefaultDuel,
    DefaultSource,
    TeamSize,
    choice_notes,
    shortfall_text,
    slot_dsls,
)
from bartleby.shared.strategy_models import DuelCombatStrategy, PlayerAssignment, PlayerCombatStrategy

HITTER = PlayerCombatStrategy(id="hitter", name="Hitter", dsl="any<damage>")
SUPPORT = PlayerCombatStrategy(id="support", name="Support", dsl="any<blade> @ ally(last)")


def _duel(*assignments: PlayerAssignment) -> DuelCombatStrategy:
    return DuelCombatStrategy(name="Team", player_assignments=list(assignments))


def _library(*strategies: PlayerCombatStrategy):
    by_id = {s.id: s for s in strategies}
    return by_id.get


def test_each_slot_runs_its_linked_player_strategy_or_its_own_dsl():
    duel = _duel(
        PlayerAssignment(players=[1], player_combat_strategy_id="hitter"),
        PlayerAssignment(players=[2, 3], player_combat_strategy_id="support"),
        PlayerAssignment(players=[4], dsl="pass"),
    )
    assert slot_dsls(duel, _library(HITTER, SUPPORT), 4) == [HITTER.dsl, SUPPORT.dsl, SUPPORT.dsl, "pass"]


def test_a_slot_with_nothing_to_run_runs_the_last_resort():
    """Unassigned, an empty inline DSL, a player strategy that can't be read, and no duel at all."""
    duel = _duel(
        PlayerAssignment(players=[1], player_combat_strategy_id="gone"),
        PlayerAssignment(players=[2], dsl=""),
    )
    assert slot_dsls(duel, _library(), 3) == [LAST_RESORT_DSL] * 3
    assert slot_dsls(None, _library(), 2) == [LAST_RESORT_DSL] * 2


def test_a_linked_player_strategy_is_looked_up_once():
    looked_up: list[str] = []

    def player(strategy_id: str):
        looked_up.append(strategy_id)
        return HITTER

    duel = _duel(
        PlayerAssignment(players=[1], player_combat_strategy_id="hitter"),
        PlayerAssignment(players=[2], player_combat_strategy_id="hitter"),
    )
    slot_dsls(duel, player, 2)
    assert looked_up == ["hitter"]


def test_the_label_names_the_duel_and_whose_default_it_is():
    duel = DuelCombatStrategy(name="Default Solo Duel")
    assert DefaultDuel(TeamSize.SOLO, duel, DefaultSource.SHIPPED).label == (
        "duel strategy 'Default Solo Duel' (the shipped Solo default)"
    )
    assert DefaultDuel(TeamSize.TEAM, duel, DefaultSource.YOURS).label == (
        "duel strategy 'Default Solo Duel' (your Team default)"
    )


def test_the_label_says_why_the_users_default_was_passed_over():
    duel = DuelCombatStrategy(name="Everyone Attacks")
    found = DefaultDuel(TeamSize.TEAM, duel, DefaultSource.SHIPPED, ("your Team default is not in the library",))
    assert found.label == (
        "duel strategy 'Everyone Attacks' (the shipped Team default; your Team default is not in the library)"
    )


def test_with_no_duel_the_label_names_the_built_in_default():
    assert DefaultDuel(TeamSize.SOLO, None, None).label == "the built-in default (no strategy file could be read)"


# -- what is wrong with a choice ------------------------------------------------------------------------------


def _notes(size: TeamSize, duel: DuelCombatStrategy | None, **words) -> list[str]:
    return choice_notes(size, duel.id if duel is not None else None, lambda _id: duel, _library(HITTER), **words)


def test_no_choice_and_a_choice_that_fits_have_no_notes():
    assert _notes(TeamSize.TEAM, None) == []
    assert _notes(TeamSize.TEAM, DuelCombatStrategy(name="Any", max_players=4)) == []
    assert _notes(TeamSize.SOLO, DuelCombatStrategy(name="Any", max_players=4)) == []


def test_a_choice_the_library_no_longer_has_says_what_is_used():
    missing = choice_notes(TeamSize.TEAM, "gone", lambda _id: None, _library(), instead="the mob duel")
    assert missing == ["It is no longer in the library, so the mob duel is used."]


@pytest.mark.parametrize(
    ("size", "low", "high", "who"),
    [
        (TeamSize.TEAM, 4, 4, "It takes 4 players, so with 2 or 3 {} {} is used."),
        (TeamSize.TEAM, 2, 3, "It takes 2 to 3 players, so with 4 {} {} is used."),
        (TeamSize.SOLO, 2, 4, "It takes 2 to 4 players, so with 1 {} {} is used."),
    ],
)
def test_a_choice_that_does_not_fit_every_size_says_when_the_other_is_used(size, low, high, who):
    duel = DuelCombatStrategy(name="Boss", min_players=low, max_players=high)
    noun = "client" if size is TeamSize.SOLO else "clients"
    assert _notes(size, duel) == [who.format(f"{noun} connected", "the recommended one")]

    words = {"instead": "the default", "nouns": ("wizard", "wizards"), "where": "in the fight"}
    noun = "wizard" if size is TeamSize.SOLO else "wizards"
    assert _notes(size, duel, **words) == [who.format(f"{noun} in the fight", "the default")]


def test_a_choice_linking_a_missing_player_strategy_says_so():
    duel = _duel(PlayerAssignment(players=[1, 2], player_combat_strategy_id="gone")).model_copy(
        update={"max_players": 4}
    )
    assert _notes(TeamSize.TEAM, duel) == [
        "It links a player strategy that is missing, so those players fight with the built-in DSL."
    ]


def test_a_shortfall_says_where_the_players_are():
    duel = DuelCombatStrategy(name="Boss", min_players=4, max_players=4)
    assert shortfall_text(duel, 2) == "'Boss' takes 4 players, and 2 are connected"
    assert shortfall_text(duel, 1, where="in the fight") == "'Boss' takes 4 players, and 1 is in the fight"
