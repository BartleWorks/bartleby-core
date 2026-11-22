"""Which kind of fight a client is in, read from its duel's participants."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bartleby.runtime.combat import fight_kind as fight_kind_module
from bartleby.runtime.combat.fight_kind import fight_kind
from bartleby.shared.combat_defaults import FightKind

OURS = 11


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(fight_kind_module, "ROUND_REPORT_RETRY_INTERVAL", 0)


def _member(owner: int, *, team: int, boss: bool = False, health: int = 500) -> SimpleNamespace:
    return SimpleNamespace(
        owner_id_full=AsyncMock(return_value=owner),
        team_id=AsyncMock(return_value=team),
        boss_mob=AsyncMock(return_value=boss),
        player_health=AsyncMock(return_value=health),
    )


def _client(*lists) -> SimpleNamespace:
    """A client whose duel lists each of *lists* in turn, one a read; an exception is raised."""
    return SimpleNamespace(
        client_object=SimpleNamespace(global_id_full=AsyncMock(return_value=OURS)),
        duel=SimpleNamespace(participant_list=AsyncMock(side_effect=list(lists))),
    )


ME = _member(OURS, team=1)


@pytest.mark.parametrize(
    ("enemies", "kind"),
    [
        ([_member(500, team=2)], FightKind.MOB),
        ([_member(500, team=2), _member(501, team=2, boss=True)], FightKind.BOSS),
        ([_member(500, team=2), _member(501, team=2, boss=True, health=0)], FightKind.MOB),
        ([_member(99, team=1, boss=True), _member(500, team=2)], FightKind.MOB),
    ],
    ids=["mobs", "a boss and a minion", "a dead boss", "a boss on our side"],
)
async def test_any_living_enemy_boss_makes_it_a_boss_fight(enemies, kind):
    assert await fight_kind(_client([ME, *enemies])) is kind


async def test_a_list_caught_mid_rebuild_is_read_again():
    boss = [ME, _member(500, team=2, boss=True)]

    assert await fight_kind(_client(RuntimeError("rebuilt"), [], boss)) is FightKind.BOSS


async def test_with_no_enemy_listed_or_nothing_readable_there_is_no_kind():
    assert await fight_kind(_client([ME], [ME], [ME])) is None
    assert await fight_kind(_client(*[RuntimeError("unreadable")] * 3)) is None
