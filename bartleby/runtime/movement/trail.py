"""The trail a leader leaves: the ground it just crossed, for followers to walk.

Followers walk the leader's trail instead of porting to it, so keeping up costs no loading
screen, and the trail needs no route planning: it is ground the leader just crossed. It is
built from the leader's state reader, a point every :data:`SPACING` units, and marks the two
places walking alone cannot follow:

* a **jump**: the leader moved further in one reading than walking allows, within its
  zone. An in-zone teleporter, a ladder, a zipline. The point it left from is marked with
  where it landed, and a follower presses Interact there.
* a **gate**: the leader's zone changed. The last point in the old zone is marked, and a
  follower walks through it after the leader.

A jump that lands back on the trail just walked is not a jump but the server's reset of a
position it would not accept (rubber-banding, ``docs/WIZARD101.md``, *Moving*): the trail is cut
back to where the leader landed instead.

Points carry a sequence number that never repeats, so each follower keeps its own cursor
into the trail while the oldest points are dropped.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import StrEnum

from bartleby.runtime.movement.geometry import Point, flat_distance
from bartleby.runtime.state import Snapshot

#: Units between two points of the trail.
SPACING = 40.0
#: Units a second faster than any walk, 2.0x included: a reading that implies more is a jump.
JUMP_SPEED = 3000.0
#: And never less than this far, so a slow reading cannot make a step look like a jump.
JUMP_MIN = 300.0
#: A landing this close to a recent point of the trail is a reset back onto it.
RESET_MATCH = 150.0
#: How many of the latest points a reset can land back on.
RESET_LOOKBACK = 60
#: Points kept. At SPACING apart, far more trail than a follower should fall behind.
MAX_POINTS = 5000
#: Units further than the nearest point a newer one may be, and still be where a follower rejoins.
REJOIN_SLACK = 80.0


class Mark(StrEnum):
    WALK = "walk"
    JUMP = "jump"
    GATE = "gate"


@dataclass
class TrailPoint:
    seq: int
    position: Point
    zone: str
    mark: Mark = Mark.WALK
    #: Where a jump landed, for a JUMP point.
    landing: Point | None = None


class Trail:
    """One leader's trail. Feed it the leader's snapshots with :meth:`note`."""

    def __init__(self) -> None:
        self.points: deque[TrailPoint] = deque(maxlen=MAX_POINTS)
        self._seq = 0
        self._last: Snapshot | None = None
        self._gap = False

    @property
    def first_seq(self) -> int:
        return self.points[0].seq if self.points else self._seq

    @property
    def end_seq(self) -> int:
        """The sequence number the next point will take."""
        return self._seq

    def _add(self, position: Point, zone: str) -> TrailPoint:
        point = TrailPoint(self._seq, position, zone)
        self._seq += 1
        self.points.append(point)
        return point

    def note(self, snap: Snapshot) -> None:
        """Take one of the leader's snapshots."""
        if not snap.readable:
            # A loading screen, or a read that failed: whatever moved the leader meanwhile
            # was not walking.
            self._gap = True
            return
        last, self._last = self._last, snap
        gap, self._gap = self._gap, False
        if not self.points:
            self._add(snap.position, snap.zone)
            return
        tail = self.points[-1]
        if snap.zone != tail.zone:
            tail.mark = Mark.GATE
            self._add(snap.position, snap.zone)
            return
        moved = flat_distance(snap.position, last.position)
        elapsed = max(snap.time - last.time, 1e-6)
        if moved > JUMP_MIN and (gap or moved / elapsed > JUMP_SPEED):
            if self._cut_back_to(snap.position):
                return
            # The last point is where it left from: make it exactly that, and mark it.
            if flat_distance(tail.position, last.position) > 1.0:
                tail = self._add(last.position, snap.zone)
            tail.mark, tail.landing = Mark.JUMP, snap.position
            self._add(snap.position, snap.zone)
            return
        if flat_distance(snap.position, tail.position) >= SPACING:
            self._add(snap.position, snap.zone)

    def _cut_back_to(self, landing: Point) -> bool:
        """Drop the points after the recent one *landing* is on, if any. True when cut."""
        zone = self.points[-1].zone
        recent = [p for p in list(self.points)[-RESET_LOOKBACK:-1] if p.zone == zone]
        near = [p for p in recent if flat_distance(p.position, landing) <= RESET_MATCH]
        if not near:
            return False
        back_to = min(near, key=lambda p: flat_distance(p.position, landing))
        while self.points and self.points[-1].seq > back_to.seq:
            self.points.pop()
        return True

    def at(self, seq: int) -> TrailPoint | None:
        if not self.points or seq < self.first_seq or seq >= self._seq:
            return None
        return self.points[seq - self.first_seq]

    def after(self, seq: int):
        """Points from *seq* on, oldest first."""
        start = max(seq, self.first_seq) - self.first_seq
        for i in range(start, len(self.points)):
            yield self.points[i]

    def rejoin(self, position: Point, zone: str, start: int) -> TrailPoint | None:
        """The point from *start* on in *zone* a follower at *position* picks the trail up at.

        The newest within :data:`REJOIN_SLACK` of the nearest, not the nearest itself: a
        leader that comes back past ground it crossed earlier, by a teleport, a zone it
        left and came back to, or a loop, leaves two passes over it, and the nearest point
        can be on the older one, which walks the follower the whole way round again.
        """
        points = [(p, flat_distance(p.position, position)) for p in self.after(start) if p.zone == zone]
        if not points:
            return None
        nearest = min(d for _, d in points)
        return next(p for p, d in reversed(points) if d <= nearest + REJOIN_SLACK)
