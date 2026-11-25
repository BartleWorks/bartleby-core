"""Planning a route across one zone, from its files: the floor, its patrols, its links, its exits.

A* over the grid's cells (8 neighbors, no cutting a wall's corner), each link an extra edge from
the floor near where it is taken to its landing, costing seconds of walking. The route is then
straightened to the points where a walker has to turn, never coming nearer a patrol than the
route itself did, and comes back as legs, each but the last ending at a link. It keeps out of
every exit of the zone but the one it is sent to, and never walks through a volume link it
does not take, since stepping in moves the wizard. Moved from BartleBot's
``tools/probe_walk_route.py``, whose ``Planner`` this is; core's ``docs/WALKING.md``,
*Planning a route*, has what each number was measured against.

A zone's gates (``gates.py``) are open until a planner is told one is closed
(:meth:`ZonePlanner.close`); a closed one is walled for every route after, until the client shows
it gone (:meth:`ZonePlanner.open`), and when one is what keeps a route from its goal,
:meth:`ZonePlanner.no_route` names it.

Planning needs no client and opens no file: :class:`ZoneFiles` is what a tool reads out of the
zone's wad.
"""

from __future__ import annotations

import dataclasses
import heapq
import math
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass, field

import numpy as np

from bartleby.runtime.movement.collision import Footprint, Solid, blocking_footprints, walkable_triangles
from bartleby.runtime.movement.gates import Crossing, Gate, NoRoute, mark_walls
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
    taken_only: Set[int] | None = None,
) -> tuple[list[Node], frozenset[int]] | None:
    """A cell-by-cell route, and each k whose step from node k to node k + 1 is a link, or None.
    *portals* maps a node's flat index to the nodes a link puts it on, and what taking it costs.
    A link's step is recorded as one, since its landing may be the next cell across from where it
    is taken, on another storey: a short ladder. The distance heuristic does not know about
    links, so a route using one is found but not always the shortest. A node in *taken_only* (a
    volume link's floor, which moves whoever steps in) is left only by a link, never walked on
    from, unless the route starts there."""
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
    by_link = {s: False}  # whether the best way yet onto a node is a link's step
    heap = [(0.0, 0.0, s)]
    done = set()
    while heap:
        _, g_cost, cur = heapq.heappop(heap)
        if cur in done:
            continue
        if cur == g:
            path, linked = [], []
            while cur != -1:
                layer, rest = divmod(cur, plane)
                path.append((layer, *divmod(rest, nx)))
                linked.append(by_link[cur])
                cur = came[cur]
            # Gathered goal first: the k-th node gathered is reached from node len - k - 2 of the route.
            return path[::-1], frozenset(len(path) - k - 2 for k, step in enumerate(linked) if step)
        done.add(cur)
        rest = cur % plane
        cj, ci = divmod(rest, nx)
        height = z[cur]
        walks_on = not (taken_only and cur in taken_only and cur != s)
        for dj, di, length in steps if walks_on else ():
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
                    by_link[nb] = False
                    heapq.heappush(heap, (ng + math.hypot(nj - gj, ni - gi) * c, ng, nb))
        for nb, extra in portals.get(cur, ()) if portals else ():
            if nb in done:
                continue
            ng = g_cost + extra
            if ng < best.get(nb, math.inf):
                best[nb] = ng
                came[nb] = cur
                by_link[nb] = True
                nj, ni = divmod(nb % plane, nx)
                heapq.heappush(heap, (ng + math.hypot(nj - gj, ni - gi) * c, ng, nb))
    return None


def link_reach(link: Link) -> float:
    """How near its source a link's floor reaches: as near as a wizard stands to take it, and
    for a volume, which moves whoever steps in, :data:`EXIT_MARGIN` more, as an exit has, so
    a route walking past keeps the wizard's body out of it."""
    return link.reach + (EXIT_MARGIN if link.kind == "volume" else 0.0)


def _flat(spots: np.ndarray) -> list[int]:
    """The flat index astar uses of every True spot of a (layer, j, i) array."""
    _, ny, nx = spots.shape
    return [int(layer * ny * nx + j * nx + i) for layer, j, i in zip(*np.nonzero(spots))]


def runs(path: Sequence[Node], jumps: Set[int]) -> list[list[Node]]:
    """*path* cut at each link: after each k of *jumps*, whose step to k + 1 is a link's. The
    cut is never guessed from the nodes, since a link's landing may be the next cell across."""
    out = [[path[0]]]
    for k, node in enumerate(path[1:]):
        if k in jumps:
            out.append([])
        out[-1].append(node)
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
    """A planned route: its cells, the floor it was planned on, and where it takes a link. The
    floor is the zone's less the exits it keeps out of; straightening a route uses the same
    floor, so a straight line never cuts through an exit the route went around. *jumps* holds
    each k whose step from node k to node k + 1 is a link's, as astar took it."""

    nodes: list[Node]
    passable: np.ndarray = field(repr=False)
    jumps: frozenset[int]


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
    gates: list[Gate] = field(default_factory=list)  # gamedata.bin's objects with a solid file


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
    gates: list[Gate] = field(default_factory=list)  # a link object's, marked wall, is in the grid's walls
    closed: list[Gate] = field(default_factory=list)  # the gates this planner was told are closed
    blocks: list[tuple[float, float, float, float]] = field(default_factory=list)  # block's (x, y, z, radius)
    # The floor spots each closed gate took when it was closed, for open to give back.
    _took: dict[Gate, np.ndarray] = field(default_factory=dict, repr=False)
    # Each gate's floor (gate_floor), shared by every copy of a planner, since the grid is.
    _gate_floors: dict[tuple, tuple[int, int, np.ndarray]] = field(default_factory=dict, repr=False)
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
        *avoid*, it pays the patrols' cost; without, it takes the shortest.

        It steps onto a volume link's floor only to take that link, since stepping in moves the
        wizard, unless the start or the goal is in it. Where that leaves no route (a volume
        across the only way, whose landing is off the floor), it plans once more as if the
        volumes were floor, and the walker deals with whatever moves it."""
        floor = self.floor_for(start, goal)
        s, g = self.node(*start, passable=floor), self.node(*goal, passable=floor)
        if s is None or g is None:
            return None
        cost = self.cost if avoid else np.ones_like(self.cost)
        portals = self.portals(floor)
        volumes = self.volume_floor(floor, s, g)
        taken_only = set(_flat(volumes))
        found = astar(self.grid, floor, cost, s, g, self.max_rise, portals, taken_only)
        if found is None and taken_only:
            volumes = np.zeros_like(floor)
            found = astar(self.grid, floor, cost, s, g, self.max_rise, portals)
        if found is None:
            return None
        nodes, jumps = found
        # Straightening must not cut across a volume the route did not step into either.
        walked = floor & ~volumes
        for node in nodes:
            walked[node] = floor[node]
        return Route(nodes, walked, jumps)

    def _link_floor(self, link: Link, floor: np.ndarray) -> np.ndarray:
        """The spots of *floor* within :func:`link_reach` of *link*'s source, at about its height."""
        px, py = self.grid.all_centers()
        near = (np.hypot(px - link.source[0], py - link.source[1]) <= link_reach(link))[None] & floor
        with np.errstate(invalid="ignore"):
            near &= np.abs(self.grid.levels - link.source[2]) <= LEVEL_GAP
        return near

    def volume_floor(self, floor: np.ndarray, *keep: Node) -> np.ndarray:
        """The spots of *floor* in a volume link, where stepping in moves the wizard: every
        volume's but those holding a node of *keep* (a route's start or goal)."""
        out = np.zeros_like(floor)
        for link in self.links:
            if link.kind != "volume":
                continue
            spots = self._link_floor(link, floor)
            if not any(spots[node] for node in keep):
                out |= spots
        return out

    def portals(self, passable: np.ndarray | None = None) -> dict[int, list[tuple[int, float]]]:
        """For astar: every passable spot within a link's reach of its source, at about its
        height, leads to the spot nearest where the link lands."""
        floor = self.passable if passable is None else passable
        _, ny, nx = floor.shape
        plane = ny * nx
        out: dict[int, list[tuple[int, float]]] = {}
        for link in self.links:
            landing = self.node(*link.target, passable=floor)
            if landing is None:
                continue
            to = landing[0] * plane + landing[1] * nx + landing[2]
            for spot in _flat(self._link_floor(link, floor)):
                out.setdefault(spot, []).append((to, link.seconds * WIZARD_SPEED))
        return out

    def turns(self, route: Route) -> list[Node]:
        """The route cut to its turns, each stretch between links straightened on its own."""
        return self.turns_and_jumps(route)[0]

    def turns_and_jumps(self, route: Route) -> tuple[list[Node], set[int]]:
        """turns(), and each k whose step from turn k to turn k + 1 is a link rather than walked."""
        out: list[Node] = []
        jumps: set[int] = set()
        for run in runs(route.nodes, route.jumps):
            if out:
                jumps.add(len(out) - 1)
            out += smooth(self.grid, route.passable, self.clearance, run, self.soft, self.max_rise)
        return out, jumps

    def legs(self, route: Route) -> list[Leg]:
        """The route as stretches to walk, each but the last ending in the link that joins it to
        the next: at a volume's center, or, for an object pressed, on the floor near it."""
        parts = runs(route.nodes, route.jumps)
        out = []
        for k, run in enumerate(parts):
            straight = smooth(self.grid, route.passable, self.clearance, run, self.soft, self.max_rise)
            points = [self.grid.world(*n) for n in straight]
            link = None
            if k + 1 < len(parts):
                lands_on = parts[k + 1][0]
                ends_at = self.grid.world(*run[-1])
                for candidate in self.links:
                    near = math.dist(candidate.source[:2], ends_at[:2]) <= link_reach(candidate) + self.grid.cell
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

    # --- gates ---------------------------------------------------------------------------------

    def gate_floor(self, gate: Gate) -> tuple[int, int, np.ndarray]:
        """The floor spots *gate* takes when closed, as (j, i, spots): *spots* a (layer, rows,
        columns) window of the grid whose corner is cell (j, i). They are the spots passable on
        the grid as built and not once the gate's footprints are walls too, which takes the
        wizard's clearance off each side, as :meth:`Grid.passable` does for every other wall.
        Built on a window around the gate, wide enough that nothing beyond it could change."""
        name = (gate.name, gate.at)
        if name not in self._gate_floors:
            self._gate_floors[name] = _gate_floor(self.grid, gate, self.max_rise)
        return self._gate_floors[name]

    def _gate_spots(self, gate: Gate) -> tuple[slice, slice, slice, np.ndarray]:
        j, i, spots = self.gate_floor(gate)
        return slice(None), slice(j, j + spots.shape[1]), slice(i, i + spots.shape[2]), spots

    def close(self, gate: Gate) -> int:
        """Wall *gate* for every route this planner plans from now on: a walk found it there.
        Returns how many floor spots it closed. A link object's gate is a wall already."""
        if gate.wall or any(g is gate for g in self.closed):
            return 0
        layer, rows, cols, spots = self._gate_spots(gate)
        taken = spots & self.passable[layer, rows, cols]
        self.passable[layer, rows, cols] &= ~spots
        self.closed.append(gate)
        self._took[gate] = taken
        return int(taken.sum())

    def open(self, gate: Gate) -> int:
        """Undo :meth:`close`: the client shows *gate* gone after all. Gives back only the floor
        closing it took, and none that another closed gate or a :meth:`block` still takes.
        Returns how many floor spots it opened; 0 for a gate that is not closed."""
        taken = self._took.pop(gate, None)
        if taken is None:
            return 0
        self.closed[:] = [g for g in self.closed if g is not gate]
        layer, rows, cols, _ = self._gate_spots(gate)
        self.passable[layer, rows, cols] |= taken
        for other in self.closed:
            o_layer, o_rows, o_cols, o_spots = self._gate_spots(other)
            self.passable[o_layer, o_rows, o_cols] &= ~o_spots
        for x, y, z, radius in self.blocks:
            self._block(x, y, z, radius)
        return int(taken.sum())

    def gates_on(self, route: Route) -> list[Gate]:
        """The gates *route* crosses, in the order it reaches them: those whose floor it steps on."""
        found: list[tuple[int, int, Gate]] = []
        nodes = np.asarray(route.nodes, dtype=np.intp).reshape(-1, 3)
        for k, gate in enumerate(self.gates):
            if gate.wall:
                continue
            first = _first_on(nodes, self.gate_floor(gate))
            if first is not None:
                found.append((first, k, gate))
        return [gate for _, _, gate in sorted(found, key=lambda t: (t[0], t[1]))]

    def gates_near(self, x: float, y: float, z: float, reach: float) -> list[Gate]:
        """The open gates within *reach* of (x, y) at about height z, nearest first."""
        near = []
        for k, gate in enumerate(self.gates):
            if gate.wall or any(g is gate for g in self.closed):
                continue
            d = gate.distance(x, y, z)
            if d <= reach:
                near.append((d, k, gate))
        return [gate for _, _, gate in sorted(near, key=lambda t: (t[0], t[1]))]

    def no_route(self, start: Sequence[float], goal: Sequence[float], avoid: bool = True) -> NoRoute:
        """Why :meth:`route` found nothing: the zone and both points, and the closed gate in the
        way, with its :class:`Crossing`, when opening that one gate alone gives a route. The
        gates closed last are tried first."""
        start3, goal3 = self._with_height(start), self._with_height(goal)
        for gate in reversed(self.closed):
            others = [g for g in self.closed if g is not gate]
            trial = self.passable.copy()
            layer, rows, cols, spots = self._gate_spots(gate)
            trial[layer, rows, cols] |= spots
            for other in others:
                o_layer, o_rows, o_cols, o_spots = self._gate_spots(other)
                trial[o_layer, o_rows, o_cols] &= ~o_spots
            found = dataclasses.replace(self, passable=trial, closed=others).route(start, goal, avoid)
            if found is None:
                continue
            nodes = np.asarray(found.nodes, dtype=np.intp).reshape(-1, 3)
            first = _first_on(nodes, self.gate_floor(gate))
            last = _first_on(nodes[::-1], self.gate_floor(gate))
            if first is None or last is None:
                continue
            last = len(found.nodes) - 1 - last
            before = self.grid.world(*found.nodes[first - 1]) if first > 0 else start3
            after = self.grid.world(*found.nodes[last + 1]) if last + 1 < len(found.nodes) else goal3
            return NoRoute(self.zone, start3, goal3, gate.name, Crossing(before, after, math.dist(before, after)))
        return NoRoute(self.zone, start3, goal3)

    def _with_height(self, point: Sequence[float]) -> XYZ:
        """*point* with a height: its own, or the floor's where it snaps to."""
        if len(point) >= 3:
            return (float(point[0]), float(point[1]), float(point[2]))
        spot = self.node(point[0], point[1])
        return (float(point[0]), float(point[1]), self.grid.world(*spot)[2] if spot is not None else 0.0)

    def block(self, x: float, y: float, z: float, radius: float) -> int:
        """Mark the floor within *radius* of (x, y), at about height z, impassable: something a
        walk ran into that the collision file does not have. Returns how many spots it closed."""
        self.blocks.append((x, y, z, radius))
        return self._block(x, y, z, radius)

    def _block(self, x: float, y: float, z: float, radius: float) -> int:
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
    grid = floor_grid(files.solids, cell)
    passable = grid.passable(radius, max_rise)
    links, exits = links_and_exits(files, grid, passable, pieces(grid, passable, max_rise))
    gates = mark_walls(files.gates, links)
    if wall_gates(grid, gates):
        passable = grid.passable(radius, max_rise)
    return assemble(
        files.zone,
        grid,
        files.patrols,
        files.named,
        links,
        exits,
        gates=gates,
        passable=passable,
        hard=hard,
        soft=soft,
        weight=weight,
        hard_weight=hard_weight,
        max_rise=max_rise,
    )


def wall_gates(grid: Grid, gates: Sequence[Gate]) -> bool:
    """Wall *grid* where each gate marked a wall stands (a link object's); whether any was.
    Done after the links are found, on the floor as ``collision.bcd`` has it, so a link object's
    own solid never hides its link: the planner takes it from the floor around."""
    walls = [f for gate in gates if gate.wall for f in gate.footprints]
    if walls:
        grid.add_walls(walls)
    return bool(walls)


def links_and_exits(
    files: ZoneFiles, grid: Grid, passable: np.ndarray, label: np.ndarray
) -> tuple[list[Link], list[Volume]]:
    """The zone's links, found on its floor, split into *label*'s :func:`pieces`, and its exits."""
    links = zone_links(files.zone, grid, passable, label, files.volumes, files.placed, files.named, files.nav)
    return links, zone_exits(files.volumes, files.teleports, links)


def floor_grid(solids: Sequence[Solid], cell: float = CELL) -> Grid:
    """The zone's floor from its ``collision.bcd``: rastered, and walled by its solids."""
    return grid_of(walkable_triangles(list(solids)), blocking_footprints(list(solids)), cell)


def grid_of(triangles: np.ndarray, footprints: Sequence[Footprint], cell: float = CELL) -> Grid:
    """The floor triangles rastered and walled by the footprints: what a shipped zone map's
    floor (``zone_maps``) is loaded into, so it is built as one from the wad is."""
    grid = Grid(triangles, cell)
    grid.add_walls(list(footprints))
    return grid


def assemble(
    zone: str,
    grid: Grid,
    patrols: Sequence[Patrol],
    named: Mapping[str, XYZ],
    links: Sequence[Link],
    exits: Sequence[Volume],
    *,
    gates: Sequence[Gate] = (),
    passable: np.ndarray | None = None,
    room: np.ndarray | None = None,
    radius: float = CLEARANCE,
    hard: float = HARD,
    soft: float = SOFT,
    weight: float = WEIGHT,
    hard_weight: float = HARD_WEIGHT,
    max_rise: float = MAX_RISE,
) -> ZonePlanner:
    """A planner from a grid already built and the links and exits already found: what
    :func:`build_planner` ends with, and what a shipped zone map (``zone_maps``) is loaded into,
    so both plan alike. *passable* is the grid's own, and *room* the patrols' :func:`clearance`
    on it, when not given: ``zone_maps`` gives both from a copy saved on the user's machine."""
    passable = grid.passable(radius, max_rise) if passable is None else passable
    room = clearance(grid, patrols) if room is None else room
    cost = danger_cost(room, hard, soft, weight, hard_weight)
    return ZonePlanner(
        zone,
        grid,
        passable,
        room,
        cost,
        list(patrols),
        dict(named),
        soft,
        max_rise,
        list(links),
        list(exits),
        list(gates),
    )


def _extent(f: Footprint) -> tuple[float, float, float, float]:
    """A footprint's bounds on the floor plan: (x low, x high, y low, y high)."""
    if f.hull is None:
        (x, y), r = f.center, f.radius
        return x - r, x + r, y - r, y + r
    hull = np.asarray(f.hull, dtype=float)
    return float(hull[:, 0].min()), float(hull[:, 0].max()), float(hull[:, 1].min()), float(hull[:, 1].max())


def _gate_floor(grid: Grid, gate: Gate, max_rise: float) -> tuple[int, int, np.ndarray]:
    """:meth:`ZonePlanner.gate_floor`, built. The window reaches the clearance and two cells
    beyond the gate's footprints, since a wall changes only the floor within the clearance of it."""
    empty = (0, 0, np.zeros((len(grid.levels), 0, 0), dtype=bool))
    if not gate.footprints:
        return empty
    bounds = [_extent(f) for f in gate.footprints]
    reach = max(1, round(CLEARANCE / grid.cell)) + 2
    j0, i0 = grid.index(min(b[0] for b in bounds), min(b[2] for b in bounds))
    j1, i1 = grid.index(max(b[1] for b in bounds), max(b[3] for b in bounds))
    i0, j0 = max(0, i0 - reach), max(0, j0 - reach)
    i1, j1 = min(grid.nx - 1, i1 + reach), min(grid.ny - 1, j1 + reach)
    if i1 < i0 or j1 < j0:  # off the grid altogether
        return empty
    window = Grid.from_arrays(
        grid.levels[:, j0 : j1 + 1, i0 : i1 + 1],
        grid.walls[:, j0 : j1 + 1, i0 : i1 + 1].copy(),
        grid.x0 + i0 * grid.cell,
        grid.y0 + j0 * grid.cell,
        grid.cell,
    )
    before = window.passable(CLEARANCE, max_rise)
    window.add_walls(list(gate.footprints))
    return j0, i0, before & ~window.passable(CLEARANCE, max_rise)


def _first_on(nodes: np.ndarray, floor: tuple[int, int, np.ndarray]) -> int | None:
    """The index of the first of *nodes*, (layer, j, i) rows, on a gate's floor, or None."""
    j, i, spots = floor
    _, rows, cols = spots.shape
    inside = (nodes[:, 1] >= j) & (nodes[:, 1] < j + rows) & (nodes[:, 2] >= i) & (nodes[:, 2] < i + cols)
    at = np.flatnonzero(inside)
    if not len(at):
        return None
    hit = spots[nodes[at, 0], nodes[at, 1] - j, nodes[at, 2] - i]
    return int(at[np.argmax(hit)]) if hit.any() else None
