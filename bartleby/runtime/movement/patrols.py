"""The mobs a zone's files say patrol it, and what passing near them costs a route.

Every mob spawner names a path; the path is a list of node ids, and the nodes carry positions
(``spawnData.xml``, ``pathData.xml``, ``pathNodeData.bin``). A spawned template is a mob if
it has ``DuelistBehavior``, whose ``m_npcProximity`` is the trigger radius the server uses.
Both start node types walk their paths, and paths loop. Moved from BartleBot's
``tools/probe_walk_route.py``; core's ``docs/WALKING.md``, *Mobs*, has the evidence.

Nothing here opens a file: the spawn files come in already parsed, and the template lookup is
the caller's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from bartleby.runtime.movement.zone_grid import Grid

#: Clearance beyond a patrol's trigger radius below which a step costs HARD_WEIGHT.
HARD = 250.0
#: Clearance beyond a patrol's trigger radius where the cost starts.
SOFT = 1300.0
#: Extra cost per unit walked at HARD, falling with the square to nil at SOFT.
WEIGHT = 8.0
#: Extra cost per unit walked inside HARD.
HARD_WEIGHT = 50.0

#: Both start node types walk their paths: type 0 starts at the path's first node (Unicorn Way's
#: ghosts), type 1 at one of its nodes (Triton Avenue's, checked by probe_mob_paths.py).
WALKING_STARTS = (0, 1)


@dataclass
class Patrol:
    """One spawner's mob and the path it walks."""

    spawner: str
    template: str
    proximity: float
    max_spawns: int
    points: np.ndarray  # (N, 3), in path order
    start_node_type: int = 0  # 0 starts at the path's first node; 1 picks one of its nodes


#: A template id to its name and trigger radius, or None when it is no duelist.
DuelistLookup = Callable[[int], tuple[str, float] | None]


def mob_patrols(
    spawners: Sequence[Mapping],
    paths: Mapping[int, Sequence[int]],
    nodes: Mapping[int, Sequence[float]],
    duelist: DuelistLookup,
    starts: tuple[int, ...] = WALKING_STARTS,
) -> list[Patrol]:
    """Every mob spawner's path, for the spawners whose start node type is in *starts*.

    *spawners* is ``spawnData.xml``'s ``m_spawners``, *paths* each path id's node ids from
    ``pathData.xml``, *nodes* each node id's position from ``pathNodeData.bin``, all as plain
    values. A spawner gives one patrol, whatever it rolls: its first entry that is a duelist on
    a known path."""
    out = []
    for spawner in spawners:
        for entry in spawner.get("m_spawnList") or []:
            info = (entry or {}).get("m_objectInfo")
            if not info:
                continue
            found = duelist(info["m_templateID.m_full"])
            kind = info["m_kStartNodeType"]
            if found is None or kind not in starts or info["m_pathID"] not in paths:
                continue
            points = np.asarray([nodes[i] for i in paths[info["m_pathID"]] if i in nodes], dtype=float)
            if len(points):
                name, radius = found
                out.append(Patrol(spawner["m_name"], name, radius, spawner["m_maxNumberOfSpawns"], points, kind))
                break
    return out


def segments(patrol: Patrol, closed: bool = True) -> np.ndarray:
    """The patrol's path as (N, 2, 2) floor-plan segments; *closed* adds the last node back to the first."""
    pts = patrol.points[:, :2]
    if len(pts) == 1:
        return np.array([[pts[0], pts[0]]])
    segs = np.stack([pts[:-1], pts[1:]], axis=1)
    if closed and len(pts) > 2:
        segs = np.concatenate([segs, [[pts[-1], pts[0]]]])
    return segs


def distance_to_segments(px: np.ndarray, py: np.ndarray, segs: np.ndarray) -> np.ndarray:
    """Each point's distance to the nearest of *segs*."""
    best = np.full(px.shape, np.inf)
    for (ax, ay), (bx, by) in segs:
        dx, dy = bx - ax, by - ay
        length2 = dx * dx + dy * dy
        t = np.zeros(px.shape) if length2 == 0 else np.clip(((px - ax) * dx + (py - ay) * dy) / length2, 0, 1)
        best = np.minimum(best, np.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


def clearance(grid: Grid, patrols: Sequence[Patrol]) -> np.ndarray:
    """Per cell, how far outside the nearest patrolling mob's own trigger radius it is, so a
    fairy (275) keeps a route further off than a ghost (200); infinite with no patrols."""
    px, py = grid.all_centers()
    out = np.full(px.shape, np.inf)
    for p in patrols:
        out = np.minimum(out, distance_to_segments(px, py, segments(p, closed=True)) - p.proximity)
    return out


def danger_cost(
    room: np.ndarray,
    hard: float = HARD,
    soft: float = SOFT,
    weight: float = WEIGHT,
    hard_weight: float = HARD_WEIGHT,
) -> np.ndarray:
    """Per cell, what a unit walked there costs, from its *room* (its :func:`clearance`): 1
    beyond *soft*, rising with the square toward *hard*, and 1 + *hard_weight* inside it."""
    ramp = np.clip((soft - room) / (soft - hard), 0, 1) ** 2
    cost = 1 + weight * ramp
    cost[room < hard] = 1 + hard_weight
    return cost
