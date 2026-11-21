"""A default duel's label in the log, and the DSL each player slot of a duel runs."""

from __future__ import annotations

from bartleby.shared.combat_defaults import (
    LAST_RESORT_DSL,
    DefaultDuel,
    DefaultSource,
    TeamSize,
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
