"""A zone's gates: the placed objects whose solids a wizard may or may not find in the way.

A placed object's template can carry a ``CollisionBehavior`` naming a solid file, and nearly
every such object in the game is one a quest removes, spawns on a requirement, or opens and
closes: a dungeon door that opens once its room's fight is won, a blocker gone once its quest
is done, a quest light that shows only while its quest is on. The game's files cannot say which
are there for a given player, and the files mark none of the difference: quest lights, and
dozens of objects named as doors, gates and blockers, carry no marker at all. So a gate is
**open until a walk finds it closed** (``ZonePlanner.close``): a farming player, past most of
them, keeps the routes the floor gives. The one exception is a link object's solid, such as a
teleporter stone's: the link proves it is there, so it is always a wall (:func:`mark_walls`).

When a closed gate is what keeps a route from its goal, the :class:`NoRoute` names it with its
:class:`Crossing`: where the floor ends on each side and how far apart, the length a hop past it
would need. Core's ``docs/WALKING.md``, *Gates*, has the survey this rests on.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import numpy as np

from bartleby.runtime.movement.collision import Footprint
from bartleby.runtime.movement.zone_grid import LEVEL_GAP

XYZ = tuple[float, float, float]

#: How near a link's source a link object stands, to be taken as that link's (a link's source
#: is the object's own location, so it is the same point but for float rounding).
SAME_SPOT = 1.0


@dataclass(frozen=True, eq=False)
class Gate:
    """A placed object's solids, where they stand: its zone tag (the template's name when it has
    none), its template's name, its location, and the footprints of its solids. *wall* is set
    for a link object's, which is always a wall."""

    name: str
    template: str
    at: XYZ
    footprints: tuple[Footprint, ...] = field(repr=False)
    wall: bool = False

    def key(self) -> tuple:
        """Everything the gate holds, as plain values, for comparing two gates."""
        return (
            self.name,
            self.template,
            self.at,
            self.wall,
            tuple(
                (
                    None if f.hull is None else tuple(map(tuple, np.asarray(f.hull).tolist())),
                    tuple(f.center),
                    f.radius,
                    f.zlo,
                    f.zhi,
                    f.firm,
                )
                for f in self.footprints
            ),
        )

    def distance(self, x: float, y: float, z: float) -> float:
        """How far (x, y) is from the nearest of the gate's footprints that spans about height
        z, on the floor plan: 0 inside one, and infinite when none spans that height."""
        best = math.inf
        for f in self.footprints:
            if not (f.zlo - LEVEL_GAP <= z <= f.zhi + LEVEL_GAP):
                continue
            if f.hull is None:
                best = min(best, max(0.0, math.hypot(x - f.center[0], y - f.center[1]) - f.radius))
            else:
                best = min(best, _polygon_distance(np.asarray(f.hull, dtype=float), x, y))
        return best


def _polygon_distance(hull: np.ndarray, x: float, y: float) -> float:
    """How far (x, y) is from a convex polygon, counterclockwise: 0 inside it."""
    a = hull
    b = np.roll(hull, -1, axis=0)
    edge = b - a
    to_point = np.array([x, y]) - a
    if np.all(edge[:, 0] * to_point[:, 1] - edge[:, 1] * to_point[:, 0] >= 0):
        return 0.0
    lengths = np.maximum((edge**2).sum(axis=1), 1e-12)
    t = np.clip((to_point * edge).sum(axis=1) / lengths, 0.0, 1.0)
    nearest = a + edge * t[:, None]
    return float(np.hypot(nearest[:, 0] - x, nearest[:, 1] - y).min())


def mark_walls(gates: Sequence[Gate], links: Sequence) -> list[Gate]:
    """*gates*, each one standing where a pressed link is taken (``"interact"`` or ``"ride"``,
    named for the same object) marked a wall: a teleporter stone, a zipline's post."""
    pressed = [link for link in links if link.kind != "volume"]

    def is_link(gate: Gate) -> bool:
        return any(link.name == gate.name and math.dist(link.source, gate.at) <= SAME_SPOT for link in pressed)

    return [replace(gate, wall=True) if is_link(gate) else gate for gate in gates]


@dataclass(frozen=True)
class Crossing:
    """The way past a closed gate: the last floor before it and the first after it, on the
    route that would cross it, and the distance between them, which is about the gate's depth
    plus the wizard's clearance on both sides."""

    before: XYZ
    after: XYZ
    length: float


@dataclass(frozen=True)
class NoRoute:
    """Why there is no route: the zone and both points, and, when one closed gate is what keeps
    the two apart, that gate's name and the :class:`Crossing` past it."""

    zone: str
    start: XYZ
    goal: XYZ
    gate: str | None = None
    crossing: Crossing | None = None

    def describe(self) -> str:
        """One line for a log."""
        if self.gate is None or self.crossing is None:
            return "nowhere on the floor joins the two"
        (bx, by, _), (ax, ay, _) = self.crossing.before, self.crossing.after
        return (
            f"the closed gate {self.gate} is in the way: {self.crossing.length:.0f} units past it, "
            f"from ({bx:.0f}, {by:.0f}) to ({ax:.0f}, {ay:.0f})"
        )
