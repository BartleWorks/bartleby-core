"""The order a leader's followers walk into its fights in: one after another, never at once.

A team that isn't an Adventure Party group sits in the order it walks in, and casts in seat
order, so the order the followers join a fight in is the order they cast in. BartleBot fixes
it by walking its clients in one at a time, in the duel's combat order. A product whose
leader the player walks in by hand can fix it only for the followers, and :class:`JoinOrder`
is how: it hands each :class:`~bartleby.runtime.movement.follow.Follower` a gate
(``Follower(join_gate=)``) that opens once the follower before it in the order has joined,
or :data:`~bartleby.runtime.timing.JOIN_ORDER_HOLD` after that one's gate opened, so one
that never gets in holds up the rest by that much and no more.

One is shared by a leader's followers, and fed the leader's snapshots (:meth:`note`), so it
knows when a fight starts and ends. The order is the product's to set, from the duel it will
fight: an empty order lets every follower walk in at once, as before.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from bartleby.runtime.state import Snapshot
from bartleby.runtime.timing import JOIN_ORDER_HOLD


class JoinOrder:
    """Which of a leader's followers may walk into its fight now: each waits for the one before.

    *in_battle* says whether a follower is in a fight. *hold* is how long after a follower's
    gate opened the next one's opens anyway.
    """

    def __init__(
        self,
        in_battle: Callable[[object], bool],
        *,
        hold: float = JOIN_ORDER_HOLD,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._in_battle = in_battle
        self._hold = hold
        self._clock = clock
        self.order: tuple = ()
        self._started: float | None = None  # when the leader's fight was first seen
        self._opened: dict = {}  # follower -> when its gate opened, this fight

    def set(self, followers: Sequence) -> None:
        """Walk *followers* in in this order; any other follower walks in at once."""
        self.order = tuple(followers)

    def note(self, leader: Snapshot) -> None:
        """Note the leader's latest snapshot: a fight it is no longer in is over."""
        if leader.in_battle:
            if self._started is None:
                self._started = self._clock()
        else:
            self._started = None
            self._opened.clear()

    def may_join(self, follower) -> bool:
        """Whether *follower* may walk into the leader's fight now. Asked while the leader is in one."""
        if follower in self._opened:
            return True
        if follower not in self.order:
            return True
        now = self._clock()
        if self._started is None:
            self._started = now
        place = self.order.index(follower)
        if place == 0 or self._in_battle(self.order[place - 1]) or now >= self._opens_by(place):
            self._opened[follower] = now
            return True
        return False

    def _opens_by(self, place: int) -> float:
        """When the gate at *place* opens at the latest: a hold after the one before it opened.

        A follower ahead that has not opened yet is counted as opening when it would at the
        latest, so a follower that is not walking in at all, never asking, holds up the rest by
        one hold, not forever.
        """
        opened = self._opened.get(self.order[0], self._started)
        for ahead in self.order[1:place]:
            opened = self._opened.get(ahead, opened + self._hold)
        return opened + self._hold
