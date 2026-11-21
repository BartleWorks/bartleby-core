"""Holding a fighter's first cast until the players joining its fight are in.

A player who walks into a fight first would cast in round one with the rest of the team
still on its way. The game restarts its planning timer each time a player joins, so
waiting costs nothing: the fighter holds its first cast until every player joining it
has got in or given up, or until AMBIENT_HELP_HOLD has passed since the later of its
first planning phase and the latest join, and it still casts in round one.

Moved from BartleBot's ``bot/ambient_combat.py`` (BartleBuddy #31, Stage 2), whose
``AmbientSession`` holds one per fighter. Pure but for one read of the fighter's duel phase.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from bartleby.runtime.timing import AMBIENT_HELP_HOLD


@dataclass
class FirstCastHold:
    """One fighter's held first cast. Build one per fighter, and ask :meth:`ready` until it says so."""

    joining: list
    """The players joining this fighter's fight."""
    started: float
    """When the fight was seen: what the hold counts from when the duel's phase cannot be read."""
    clock: Callable[[], float] = time.monotonic
    planning_at: float | None = None
    """When the fighter was first seen planning, once it has been."""
    released: bool = False

    async def ready(self, fighter, settled: Callable[[object], bool], joined_at: Mapping[object, float]) -> bool:
        """Whether *fighter* may start casting: everyone joining it is settled, or the hold ran out.

        *settled* says whether a joining player has got in or given up. *joined_at* holds when
        each one read in battle, by the same clock, since the game's timer restarts at each join.
        Once released, it stays released.
        """
        if self.released:
            return True
        now = self.clock()
        if all(settled(player) for player in self.joining):
            self.released = True
            return True
        if self.planning_at is None:
            try:
                from wizwalker.memory import DuelPhase  # noqa: PLC0415

                if await fighter.duel.duel_phase() == DuelPhase.planning:
                    self.planning_at = now
            except Exception:
                self.planning_at = self.started  # unreadable: count from when the fight was seen
        if self.planning_at is not None:
            anchor = max([self.planning_at, *(joined_at.get(player, 0.0) for player in self.joining)])
            self.released = now >= anchor + AMBIENT_HELP_HOLD
        return self.released
