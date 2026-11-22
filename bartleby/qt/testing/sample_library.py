"""A small strategy library of sample data, for a render sheet that draws the Combat Strategies window.

Each product's sheet draws the window over the same library, so the two can be set side by
side: an Official duel, one the user has modified and one of the user's own, with the player
strategies and decks they need. The ids are fixed and nothing is written with a date in it, so
two runs of a sheet agree. Nothing here is a player's, and none of it is game art.
"""

from __future__ import annotations

from pathlib import Path

from bartleby.shared import strategy_library
from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import (
    DeckData,
    DeckPreset,
    DuelCombatStrategy,
    PlayerAssignment,
    PlayerCombatStrategy,
)

#: The entry each tab of a sheet's window opens on.
TEAM_DUEL_ID = "00000000-0000-4000-8000-000000000001"
SOLO_DUEL_ID = "00000000-0000-4000-8000-000000000002"
OWN_DUEL_ID = "00000000-0000-4000-8000-000000000003"
HITTER_ID = "00000000-0000-4000-8000-000000000011"
SUPPORT_ID = "00000000-0000-4000-8000-000000000012"
HEALER_ID = "00000000-0000-4000-8000-000000000013"
DECK_ID = "00000000-0000-4000-8000-000000000021"

HIT = "any<damage&aoe>[any<mod_damage>] @ aoe | any<damage>[any<mod_damage>] @ enemy"
BUFF = "any<blade> @ ally(last) | any<trap> @ boss | pass"
HEAL = "any<heal> @ ally(last) | any<blade> @ self | pass"


def _official() -> list[tuple[StrategyKind, object]]:
    return [
        (
            StrategyKind.PLAYER,
            PlayerCombatStrategy(
                id=HITTER_ID, name="Sample Hitter", description="Hits every turn", dsl=HIT, tags=["Hitter"]
            ),
        ),
        (
            StrategyKind.PLAYER,
            PlayerCombatStrategy(
                id=SUPPORT_ID,
                name="Sample Support",
                description="Blades the hitter and traps the boss",
                dsl=BUFF,
                schools=["Ice", "Balance"],
                tags=["Support"],
            ),
        ),
        (
            StrategyKind.DUEL,
            DuelCombatStrategy(
                id=TEAM_DUEL_ID,
                name="Sample Team Duel",
                description="One hitter, and everyone else buffs",
                min_players=2,
                max_players=4,
                combat_order=[2, 3, 4, 1],
                player_assignments=[
                    PlayerAssignment(players=[1], player_combat_strategy_id=HITTER_ID, schools=["Storm", "Fire"]),
                    PlayerAssignment(
                        players=[2, 3, 4], player_combat_strategy_id=SUPPORT_ID, note="P2 passes for pips in round 1"
                    ),
                ],
            ),
        ),
        (
            StrategyKind.DUEL,
            DuelCombatStrategy(
                id=SOLO_DUEL_ID,
                name="Sample Solo Duel",
                description="One wizard alone",
                max_players=1,
                player_assignments=[PlayerAssignment(players=[1], player_combat_strategy_id=HITTER_ID)],
            ),
        ),
    ]


def _custom() -> list[tuple[StrategyKind, object]]:
    return [
        (
            StrategyKind.PLAYER,
            PlayerCombatStrategy(id=HEALER_ID, name="My Healer", dsl=HEAL, schools=["Life"], tags=["Healer"]),
        ),
        (
            StrategyKind.DUEL,
            DuelCombatStrategy(
                id=OWN_DUEL_ID,
                name="My Four Box",
                max_players=4,
                player_assignments=[
                    PlayerAssignment(players=[1, 2, 3], dsl=HIT),
                    PlayerAssignment(players=[4], player_combat_strategy_id=HEALER_ID),
                ],
            ),
        ),
        (
            StrategyKind.DECK,
            DeckPreset(
                id=DECK_ID,
                name="My Storm Deck",
                description="All hits",
                schools=["Storm"],
                tags=["Hitter"],
                deck=DeckData(
                    normal={"Tempest": 4, "Storm Lord": 2, "Stormblade": 3},
                    item={"Stormtrap": 1},
                    archmastery_school="Storm",
                ),
            ),
        ),
    ]


def write_sample_library(shipped: Path, user: Path) -> None:
    """Write the sample library: the Official half under *shipped*, the user's under *user*.

    ``Sample Solo Duel`` is in both, so one card wears Official (modified).
    """
    for kind, item in _official():
        strategy_library.save(shipped, kind, item)
    for kind, item in _custom():
        strategy_library.save(user, kind, item, shipped=shipped)
    solo = next(item for _kind, item in _official() if item.id == SOLO_DUEL_ID)
    solo.description = "One wizard alone, the way I play it"
    strategy_library.save(user, StrategyKind.DUEL, solo, shipped=shipped)
