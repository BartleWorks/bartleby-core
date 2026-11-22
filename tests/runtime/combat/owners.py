"""A stand-in :class:`~bartleby.runtime.combat.strategy_workset.StrategyOwner`, for the working set's and summaries' tests.

BartleBot's Bot Combat Strategy is the real one, and its adapter is tested there. This one
holds its own duels and players in lists, names library duels by id, and records its saves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import uuid4

from bartleby.shared.strategy_library import StrategyKind
from bartleby.shared.strategy_models import DuelCombatStrategy, PlayerCombatStrategy


@dataclass
class FakeOwner:
    name: str = "Farm"
    duels: list[DuelCombatStrategy] = field(default_factory=list)
    players: list[PlayerCombatStrategy] = field(default_factory=list)
    #: The library duels it names itself, as a Bot Combat Strategy's default and fights do.
    names: set[str] = field(default_factory=set)
    id: str = field(default_factory=lambda: str(uuid4()))
    #: Every snapshot :meth:`save` wrote, shared with its copies so a test sees the working set's saves.
    saved: list[str] = field(default_factory=list)
    fail: bool = False

    nouns = ("Bot Combat Strategy", "Bot Combat Strategies")
    source = "This bot"

    def own(self, kind: StrategyKind) -> list:
        return self.duels if kind is StrategyKind.DUEL else self.players

    def refers_to(self, duel_id: str) -> bool:
        return duel_id in self.names

    def snapshot(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "names": sorted(self.names),
                "duels": [d.model_dump() for d in self.duels],
                "players": [p.model_dump() for p in self.players],
            },
            sort_keys=True,
            default=str,
        )

    def copy(self) -> FakeOwner:
        return FakeOwner(
            name=self.name,
            duels=[d.model_copy(deep=True) for d in self.duels],
            players=[p.model_copy(deep=True) for p in self.players],
            names=set(self.names),
            id=self.id,
            saved=self.saved,
            fail=self.fail,
        )

    def save(self) -> None:
        if self.fail:
            raise OSError("disk full")
        self.saved.append(self.snapshot())
