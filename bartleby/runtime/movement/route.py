"""Planning a route across one zone, from its files: the floor, its patrols, its links, its exits.

A* over the grid's cells (8 neighbors, no cutting a wall's corner), each link an extra edge from
the floor near where it is taken to its landing, costing seconds of walking. The route is then
straightened to the points where a walker has to turn, never coming nearer a patrol than the
route itself did, and comes back as legs, each but the last ending at a link. It keeps out of
every exit of the zone but the one it is sent to. Moved from BartleBot's
``tools/probe_walk_route.py``, whose ``Planner`` this is; core's ``docs/WALKING.md``,
*Planning a route*, has what each number was measured against.

Planning needs no client and opens no file: :class:`ZoneFiles` is what a tool reads out of the
zone's wad.
"""

from __future__ import annotations

import dataclasses
import heapq
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from bartleby.runtime.movement.collision import Solid, blocking_footprints, walkable_triangles
from bartleby.runtime.movement.patrols import HARD, HARD_WEIGHT, SOFT, WEIGHT, Patrol, clearance, danger_cost
from bartleby.runtime.movement.zone_grid import (
    CELL,
    CLEARANCE,
    LEVEL_GAP,
    MAX_RISE,
    STEP_TOL,
    Grid,
    Node,
    nearest_node,
    pieces,
)
from bartleby.runtime.movement.zone_links import (
    XYZ,
    Link,
    PlacedObject,
    Volume,
    ZoneNav,
    zone_exits,
    zone_links,
)

#: Units a second on foot, unmounted, counting only horizontal movement (a stair's height is
#: free): what a link's seconds cost a route in distance. Measured at 600 on flat ground in
#: Unicorn Way and 626 in a house; wizwalker says 580. A mount multiplies it, most by 1.4.
WIZARD_SPEED = 600.0
#: How much further than its own size an exit is kept clear of: the wizard's own body.
EXIT_MARGIN = CLEARANCE


def astar(
    grid: Grid,
    passable: np.ndarray,
    cost: np.ndarray,
    start: Node,
    goal: Node,
    max_rise: float = MAX_RISE,
    portals: Mapping[int, list[tuple[int, float]]] | None = None,
) -> list[Node] | None:
    """A cell-by-cell route, or None. *portals* maps a node's flat index to the nodes a link
    puts it on, and what taking it costs; a route through one has two non-adjacent nodes in a
    row. The distance heuristic does not know about links, so a route using one is found but
    not always the shortest."""
    layers, ny, nx = grid.levels.shape
    plane = ny * nx
    ok = passable.ravel().tolist()
    z = np.nan_to_num(grid.levels).ravel().tolist()
    mult = cost.ravel().tolist()
    s = start[0] * plane + start[1] * nx + start[2]
    g = goal[0] * plane + goal[1] * nx + goal[2]
    _, gj, gi = goal
    c = grid.cell
    steps = [(dj, di, math.hypot(dj, di) * c) for dj in (-1, 0, 1) for di in (-1, 0, 1) if dj or di]

    def standing(j: int, i: int, height: float, tol: float) -> bool:
        return any(ok[n] and abs(z[n] - height) <= tol for n in range(j * nx + i, layers * plane, plane))

    best = {s: 0.0}
    came = {s: -1}
    heap = [(0.0, 0.0, s)]
    done = set()
    while heap:
        _, g_cost, cur = heapq.heappop(heap)
        if cur in done:
            continue
        if cur == g:
            path = []
            while cur != -1:
                layer, rest = divmod(cur, plane)
                path.append((layer, *divmod(rest, nx)))
                cur = came[cur]
            return path[::-1]
        done.add(cur)
        rest = cur % plane
        cj, ci = divmod(rest, nx)
        height = z[cur]
        for dj, di, length in steps:
            nj, ni = cj + dj, ci + di
            if not (0 <= nj < ny and 0 <= ni < nx):
                continue
            tol = max_rise * length + STEP_TOL
            if dj and di and not (standing(cj, ni, height, tol) and standing(nj, ci, height, tol)):
                continue  # no cutting a wall's corner
            for nb in range(nj * nx + ni, layers * plane, plane):
                if nb in done or not ok[nb] or abs(z[nb] - height) > tol:
                    continue
                ng = g_cost + length * (mult[rest] + mult[nj * nx + ni]) / 2
                if ng < best.get(nb, math.inf):
                    best[nb] = ng
                    came[nb] = cur
                    heapq.heappush(heap, (ng + math.hypot(nj - gj, ni - gi) * c, ng, nb))
        for nb, extra in portals.get(cur, ()) if portals else ():
            if nb in done:
                continue
            ng = g_cost + extra
            if ng < best.get(nb, math.inf):
                best[nb] = ng
                came[nb] = cur
                nj, ni = divmod(nb % plane, nx)
                heapq.heappush(heap, (ng + math.hypot(nj - gj, ni - gi) * c, ng, nb))
    return None


def runs(path: Sequence[Node]) -> list[list[Node]]:
    """*path* cut where it jumps: at each link, the next node is not the last one's neighbor."""
    out = [[path[0]]]
    for a, b in zip(path, path[1:]):
        if abs(b[1] - a[1]) > 1 or abs(b[2] - a[2]) > 1:
            out.append([])
        out[-1].append(b)
    return out


def straight_ok(
    grid: Grid,
    passable: np.ndarray,
    room: np.ndarray,
    a: Node,
    b: Node,
    least: float,
    max_rise: float = MAX_RISE,
) -> bool:
    """Whether a wizard can walk from a to b in a straight line, never passing where the
    :func:`~bartleby.runtime.movement.patrols.clearance` is under *least*, nor stepping up or
    down more than a walk allows."""
    n = int(max(abs(b[1] - a[1]), abs(b[2] - a[2])) * 2) + 1
    js = np.rint(np.linspace(a[1], b[1], n)).astype(int)
    is_ = np.rint(np.linspace(a[2], b[2], n)).astype(int)
    if (room[js, is_] < least).any():
        return False
    step = math.hypot(b[1] - a[1], b[2] - a[2]) * grid.cell / max(n - 1, 1)
    tol = max_rise * max(step, grid.cell) + STEP_TOL
    if len(grid.levels) == 1:
        z = grid.levels[0, js, is_]
        return bool(passable[0, js, is_].all() and (np.abs(np.diff(z)) <= tol).all())
    # Several floors: follow the floor at about the height walked so far, cell by cell.
    heights = np.where(passable[:, js, is_], grid.levels[:, js, is_], np.nan).T.tolist()
    z = float(grid.levels[a])
    for column in heights:
        nearest = min((abs(h - z), h) for h in column if h == h) if any(h == h for h in column) else None
        if nearest is None or nearest[0] > tol:
            return False
        z = nearest[1]
    return abs(z - float(grid.levels[b])) <= tol


def smooth(
    grid: Grid,
    passable: np.ndarray,
    room: np.ndarray,
    path: Sequence[Node],
    soft: float = SOFT,
    max_rise: float = MAX_RISE,
) -> list[Node]:
    """Cut a cell-by-cell path down to the points where a walker has to turn.

    From each kept point, the next is the furthest point along the path still reachable in a
    straight line. The line may not pass closer to a patrol than the path itself did between
    the two, so straightening never trades safety for distance; beyond *soft* it does not
    matter, as the cost there is nil."""
    if len(path) <= 2:
        return list(path)
    along = [float(room[j, i]) for _, j, i in path]
    out = [path[0]]
    a = 0
    while a < len(path) - 1:
        b = a + 1
        least = along[a]
        for k in range(a + 1, len(path)):
            least = min(least, along[k])
            if k > a + 1 and not straight_ok(
                grid, passable, room, path[a], path[k], min(least, soft) - grid.cell, max_rise
            ):
                break
            b = k
        out.append(path[b])
        a = b
    return out


@dataclass
class Leg:
    """A stretch walked: its waypoints, and the link stepped into at its end (None for the last)."""

    waypoints: list[XYZ]
    link: Link | None = None


@dataclass(frozen=True)
class Route:
    """A planned route: its cells, and the floor it was planned on, which the zone's floor less
    the exits it keeps out of. Straightening a route uses the same floor, so a straight line
    never cuts through an exit the route went around."""

    nodes: list[Node]
    passable: np.ndarray = field(repr=False)


@dataclass(frozen=True)
class ZoneFiles:
    """What a zone's planner is built from, already read out of its wad by a tool."""

    zone: str
    solids: list[Solid]  # collision.bcd
    patrols: list[Patrol]  # spawnData.xml, pathData.xml, pathNodeData.bin
    named: dict[str, XYZ]  # gamedata.bin's arrival points
    volumes: list[Volume] = field(default_factory=list)  # volumes.xml
    teleports: frozenset[str] = frozenset()  # triggers.xml
    placed: list[PlacedObject] = field(default_factory=list)  # gamedata.bin's objects, interactable
    nav: ZoneNav | None = None  # zone.nav


@dataclass
class ZonePlanner:
    """One zone's grid, walls, patrols, links and exits, built once and asked for as many
    routes as needed."""

    zone: str
    grid: Grid
    passable: np.ndarray  # per floor: (layer, j, i)
    clearance: np.ndarray  # per cell: distance beyond the nearest patrol's trigger radius
    cost: np.ndarray  # per cell
    patrols: list[Patrol]
    named: dict[str, XYZ]
    soft: float = SOFT
    max_rise: float = MAX_RISE
    links: list[Link] = field(default_factory=list)
    exits: list[Volume] = field(default_factory=list)
    _exit_floors: dict[int, np.ndarray] = field(default_factory=dict, init=False, repr=False)

    def node(self, x: float, y: float, z: float | None = None, passable: np.ndarray | None = None) -> Node | None:
        return nearest_node(self.grid, self.passable if passable is None else passable, x, y, z)

    def exit_floor(self, exit: Volume) -> np.ndarray:
        """The floor spots within *exit*, and EXIT_MARGIN of it, at about its height."""
        if id(exit) not in self._exit_floors:
            px, py = self.grid.all_centers()
            cx, cy, cz = exit.center
            across = (np.hypot(px - cx, py - cy) <= exit.reach + EXIT_MARGIN)[None]
            with np.errstate(invalid="ignore"):
                self._exit_floors[id(exit)] = across & (np.abs(self.grid.levels - cz) <= exit.rise + EXIT_MARGIN)
        return self._exit_floors[id(exit)]

    def exits_at(self, x: float, y: float, z: float | None = None) -> list[Volume]:
        """The exits whose floor holds the spot a point snaps to."""
        spot = self.node(x, y, z)
        return [] if spot is None else [e for e in self.exits if self.exit_floor(e)[spot]]

    def floor_for(self, start: Sequence[float], goal: Sequence[float]) -> np.ndarray:
        """The floor a route from *start* to *goal* may use: every exit closed but one the goal
        is in, which the route ends at, and one the start is in, which it walks out of."""
        open_ = {id(e) for e in self.exits_at(*start)} | {id(e) for e in self.exits_at(*goal)}
        out = self.passable.copy()
        for exit in self.exits:
            if id(exit) not in open_:
                out &= ~self.exit_floor(exit)
        return out

    def route(self, start: Sequence[float], goal: Sequence[float], avoid: bool = True) -> Route | None:
        """A cell-by-cell route between two points, each (x, y) or (x, y, z), or None. With
        *avoid*, it pays the patrols' cost; without, it takes the shortest."""
        floor = self.floor_for(start, goal)
        s, g = self.node(*start, passable=floor), self.node(*goal, passable=floor)
        if s is None or g is None:
            return None
        cost = self.cost if avoid else np.ones_like(self.cost)
        nodes = astar(self.grid, floor, cost, s, g, self.max_rise, self.portals(floor))
        return None if nodes is None else Route(nodes, floor)

    def portals(self, passable: np.ndarray | None = None) -> dict[int, list[tuple[int, float]]]:
        """For astar: every passable spot within a link's reach of its source, at about its
        height, leads to the spot nearest where the link lands."""
        floor = self.passable if passable is None else passable
        layers, ny, nx = floor.shape
        plane = ny * nx
        px, py = self.grid.all_centers()
        out: dict[int, list[tuple[int, float]]] = {}
        for link in self.links:
            landing = self.node(*link.target, passable=floor)
            if landing is None:
                continue
            to = landing[0] * plane + landing[1] * nx + landing[2]
            near = (np.hypot(px - link.source[0], py - link.source[1]) <= link.reach)[None] & floor
            with np.errstate(invalid="ignore"):
                near &= np.abs(self.grid.levels - link.source[2]) <= LEVEL_GAP
            for layer, j, i in zip(*np.nonzero(near)):
                out.setdefault(int(layer * plane + j * nx + i), []).append((to, link.seconds * WIZARD_SPEED))
        return out

    def turns(self, route: Route) -> list[Node]:
        """The route cut to its turns, each stretch between links straightened on its own."""
        return self.turns_and_jumps(route)[0]

    def turns_and_jumps(self, route: Route) -> tuple[list[Node], set[int]]:
        """turns(), and each k whose step from turn k to turn k + 1 is a link rather than walked."""
        out: list[Node] = []
        jumps: set[int] = set()
        for run in runs(route.nodes):
            if out:
                jumps.add(len(out) - 1)
            out += smooth(self.grid, route.passable, self.clearance, run, self.soft, self.max_rise)
        return out, jumps

    def legs(self, route: Route) -> list[Leg]:
        """The route as stretches to walk, each but the last ending in the link that joins it to
        the next: at a volume's center, or, for an object pressed, on the floor near it."""
        parts = runs(route.nodes)
        out = []
        for k, run in enumerate(parts):
            straight = smooth(self.grid, route.passable, self.clearance, run, self.soft, self.max_rise)
            points = [self.grid.world(*n) for n in straight]
            link = None
            if k + 1 < len(parts):
                lands_on = parts[k + 1][0]
                ends_at = self.grid.world(*run[-1])
                for candidate in self.links:
                    near = math.dist(candidate.source[:2], ends_at[:2]) <= candidate.reach + self.grid.cell
                    if near and self.node(*candidate.target, passable=route.passable) == lands_on:
                        link = candidate
                        break
                if link is not None and link.kind == "volume":
                    points.append((link.source[0], link.source[1], points[-1][2]))
            out.append(Leg(points, link))
        return out

    def waypoints(self, route: Route) -> list[XYZ]:
        """Every leg's waypoints in a row, start first."""
        return [p for leg in self.legs(route) for p in leg.waypoints]

    def drop(self, link: Link) -> None:
        """Stop routing through *link*: stepping into it moved nobody."""
        self.links = [other for other in self.links if other != link]

    def around(self, discs: Sequence[tuple[float, float, float]]) -> ZonePlanner:
        """A copy whose floor is closed within each (x, y, radius) of *discs*, on every storey:
        the live mobs, to route around where they are now. The original is untouched."""
        px, py = self.grid.all_centers()
        closed = np.zeros(px.shape, dtype=bool)
        for x, y, radius in discs:
            closed |= np.hypot(px - x, py - y) <= radius
        return dataclasses.replace(self, passable=self.passable & ~closed[None])

    def block(self, x: float, y: float, z: float, radius: float) -> int:
        """Mark the floor within *radius* of (x, y), at about height z, impassable: something a
        walk ran into that the collision file does not have. Returns how many spots it closed."""
        grid = self.grid
        px, py = grid.all_centers()
        near = np.hypot(px - x, py - y) <= radius
        with np.errstate(invalid="ignore"):
            hit = near[None] & (np.abs(grid.levels - z) <= LEVEL_GAP / 2) & self.passable
        self.passable &= ~hit
        return int(hit.sum())


def build_planner(
    files: ZoneFiles,
    *,
    cell: float = CELL,
    radius: float = CLEARANCE,
    hard: float = HARD,
    soft: float = SOFT,
    weight: float = WEIGHT,
    hard_weight: float = HARD_WEIGHT,
    max_rise: float = MAX_RISE,
) -> ZonePlanner:
    """A zone's planner, from its files. The keywords are the probes' to vary; a run uses the defaults."""
    grid = Grid(walkable_triangles(files.solids), cell)
    grid.add_walls(blocking_footprints(files.solids))
    passable = grid.passable(radius, max_rise)
    room = clearance(grid, files.patrols)
    cost = danger_cost(room, hard, soft, weight, hard_weight)
    links = zone_links(
        files.zone,
        grid,
        passable,
        pieces(grid, passable, max_rise),
        files.volumes,
        files.placed,
        files.named,
        files.nav,
    )
    exits = zone_exits(files.volumes, files.teleports, links)
    return ZonePlanner(
        files.zone, grid, passable, room, cost, list(files.patrols), dict(files.named), soft, max_rise, links, exits
    )
