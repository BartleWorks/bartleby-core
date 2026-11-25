"""A zone's floor as a grid of cells, every floor of a cell kept.

A cell can hold several floors (a house's two storeys, a bridge over a street), so the grid is
layered: ``levels[l, j, i]`` is the l-th floor up at cell (j, i), or nan. A layer number only
orders the floors within one cell and does not name a storey, since a stair cell has one floor
where its neighbors have two. So walking is decided by height: a step goes to the neighbor's
floor at about the same height, which is what lets a stair, and nothing else, join two
storeys. Moved from BartleBot's ``tools/probe_walk_route.py``.
"""

from __future__ import annotations

import math
from collections import deque

import numpy as np

from bartleby.runtime.movement.collision import Z_BAND_DOWN, Z_BAND_UP, Z_BOX_TOL, Z_EMBED_TOL, Footprint

#: The grid's cell, in world units.
CELL = 40.0
#: How far a wizard keeps from a wall or a drop: 49 measured against a tree.
CLEARANCE = 50.0
#: How much a step may rise or drop per unit walked, on top of STEP_TOL.
MAX_RISE = 1.0
# Two surfaces this far apart at one cell are two floors (a house's storeys, a bridge over a street).
LEVEL_GAP = 150.0
# A step may rise or drop this much on top of MAX_RISE per unit walked: a stair tread, a kerb.
STEP_TOL = 20.0
# Snapping a point to the grid, a unit of height costs this many units of distance, so a
# point keeps to its own storey rather than the nearer floor above or below it.
Z_RANK = 2.0

Node = tuple[int, int, int]  # (layer, j, i)


class Grid:
    """The floor triangles rastered onto CELL-sized cells, with the solids that wall them."""

    def __init__(self, tris: np.ndarray, cell: float = CELL) -> None:
        self.cell = cell
        self.x0, self.y0 = tris[:, :, 0].min() - cell, tris[:, :, 1].min() - cell
        self.nx = int((tris[:, :, 0].max() - self.x0) / cell) + 2
        self.ny = int((tris[:, :, 1].max() - self.y0) / cell) + 2
        hits = [hit for tri in tris if (hit := self._raster(tri)) is not None]
        self.levels = self._stack(*(np.concatenate(parts) for parts in zip(*hits)))
        self.walls = np.zeros(self.levels.shape, dtype=bool)

    @property
    def top(self) -> np.ndarray:
        """The highest floor at each cell, or nan."""
        return np.fmax.reduce(self.levels, axis=0)

    @property
    def floors(self) -> np.ndarray:
        """How many floors each cell has."""
        return (~np.isnan(self.levels)).sum(axis=0)

    def centers(self, i0: int, i1: int, j0: int, j1: int) -> tuple[np.ndarray, np.ndarray]:
        xs = self.x0 + (np.arange(i0, i1 + 1) + 0.5) * self.cell
        ys = self.y0 + (np.arange(j0, j1 + 1) + 0.5) * self.cell
        return np.meshgrid(xs, ys)

    def _span(self, lo_x: float, hi_x: float, lo_y: float, hi_y: float) -> tuple[int, int, int, int]:
        i0 = max(0, int((lo_x - self.x0) / self.cell))
        i1 = min(self.nx - 1, int((hi_x - self.x0) / self.cell))
        j0 = max(0, int((lo_y - self.y0) / self.cell))
        j1 = min(self.ny - 1, int((hi_y - self.y0) / self.cell))
        return i0, i1, j0, j1

    def _raster(self, tri: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        """(j, i, z) of every cell center the triangle covers, z being the triangle's height there."""
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
        i0, i1, j0, j1 = self._span(min(ax, bx, cx), max(ax, bx, cx), min(ay, by, cy), max(ay, by, cy))
        if i1 < i0 or j1 < j0:
            return None
        px, py = self.centers(i0, i1, j0, j1)
        det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if abs(det) < 1e-9:
            return None
        s = ((px - ax) * (cy - ay) - (cx - ax) * (py - ay)) / det
        t = ((bx - ax) * (py - ay) - (px - ax) * (by - ay)) / det
        inside = (s >= -1e-6) & (t >= -1e-6) & (s + t <= 1 + 1e-6)
        if not inside.any():
            return None
        jj, ii = np.meshgrid(np.arange(j0, j1 + 1), np.arange(i0, i1 + 1), indexing="ij")
        z = az + s * (bz - az) + t * (cz - az)
        return jj[inside], ii[inside], z[inside]

    def _stack(self, j: np.ndarray, i: np.ndarray, z: np.ndarray) -> np.ndarray:
        """Sort each cell's heights into floors, LEVEL_GAP apart, keeping each floor's highest."""
        key = j * self.nx + i
        order = np.lexsort((z, key))
        key, z = key[order], z[order]
        new_cell = np.r_[True, key[1:] != key[:-1]]
        new_floor = new_cell | np.r_[True, np.diff(z) > LEVEL_GAP]
        floor_id = np.cumsum(new_floor) - 1
        layer = floor_id - floor_id[new_cell][np.cumsum(new_cell) - 1]
        last = np.r_[new_floor[1:], True]
        levels = np.full((int(layer.max()) + 1, self.ny, self.nx), np.nan)
        levels[layer[last], key[last] // self.nx, key[last] % self.nx] = z[last]
        return levels

    def add_walls(self, footprints: list[Footprint]) -> int:
        """Wall every floor spot a footprint stands on at about its height; how many touched one."""
        blocked = 0
        for f in footprints:
            if f.hull is None:
                (x, y), r = f.center, f.radius
                i0, i1, j0, j1 = self._span(x - r, x + r, y - r, y + r)
            else:
                i0, i1, j0, j1 = self._span(
                    f.hull[:, 0].min(), f.hull[:, 0].max(), f.hull[:, 1].min(), f.hull[:, 1].max()
                )
            if i1 < i0 or j1 < j0:
                continue
            px, py = self.centers(i0, i1, j0, j1)
            if f.hull is None:
                inside = np.hypot(px - x, py - y) <= r
            else:
                inside = np.ones(px.shape, dtype=bool)
                hull = f.hull
                for k in range(len(hull)):
                    (ax, ay), (bx, by) = hull[k], hull[(k + 1) % len(hull)]
                    inside &= (bx - ax) * (py - ay) - (by - ay) * (px - ax) >= 0
            touched = False
            for layer in range(len(self.levels)):
                z = self.levels[layer, j0 : j1 + 1, i0 : i1 + 1]
                with np.errstate(invalid="ignore"):
                    embedded = (f.zlo <= z) & ((f.zhi - z) >= (Z_BOX_TOL if f.firm else Z_EMBED_TOL))
                    overhead = (z < f.zlo) & ((f.zlo - z) <= Z_BAND_UP)
                    hit = inside & ~np.isnan(z) & (z <= f.zhi + Z_BAND_DOWN) & (embedded | overhead)
                if hit.any():
                    self.walls[layer, j0 : j1 + 1, i0 : i1 + 1] |= hit
                    touched = True
            blocked += touched
        return blocked

    def passable(self, radius: float = CLEARANCE, max_rise: float = MAX_RISE) -> np.ndarray:
        """Floor that is not wall, and that has floor at about its own height all around it
        within the player's radius: away from walls, and from the edge of a drop."""
        ok = ~np.isnan(self.levels) & ~self.walls
        out = ok.copy()
        r = max(1, round(radius / self.cell))
        for dj in range(-r, r + 1):
            for di in range(-r, r + 1):
                if not (di or dj) or di * di + dj * dj > r * r:
                    continue
                tol = max_rise * math.hypot(dj, di) * self.cell + STEP_TOL
                near_z = np.roll(self.levels, (dj, di), axis=(1, 2))
                near_ok = np.roll(ok, (dj, di), axis=(1, 2))
                for layer in range(len(self.levels)):
                    support = np.zeros((self.ny, self.nx), dtype=bool)
                    with np.errstate(invalid="ignore"):
                        for other in range(len(self.levels)):
                            support |= near_ok[other] & (np.abs(near_z[other] - self.levels[layer]) <= tol)
                    out[layer] &= support
        return out

    def index(self, x: float, y: float) -> tuple[int, int]:
        return int((y - self.y0) / self.cell), int((x - self.x0) / self.cell)

    def world(self, layer: int, j: int, i: int) -> tuple[float, float, float]:
        return self.x0 + (i + 0.5) * self.cell, self.y0 + (j + 0.5) * self.cell, float(self.levels[layer, j, i])

    def all_centers(self) -> tuple[np.ndarray, np.ndarray]:
        return self.centers(0, self.nx - 1, 0, self.ny - 1)


def nearest_node(grid: Grid, passable: np.ndarray, x: float, y: float, z: float | None = None) -> Node | None:
    """The passable floor nearest (x, y), weighing height so a point keeps to its own storey."""
    ls, js, is_ = np.nonzero(passable)
    if not len(js):
        return None
    j, i = grid.index(x, y)
    d = (js - j) ** 2 + (is_ - i) ** 2
    if z is not None:
        d = d + ((grid.levels[ls, js, is_] - z) / grid.cell * Z_RANK) ** 2
    k = int(np.argmin(d))
    return int(ls[k]), int(js[k]), int(is_[k])


def pieces(grid: Grid, passable: np.ndarray, max_rise: float = MAX_RISE) -> np.ndarray:
    """Each passable floor spot's connected piece, -1 elsewhere: a route's step rule, flood-filled."""
    layers, ny, nx = passable.shape
    plane = ny * nx
    label = np.full(layers * plane, -1, dtype=np.int32)
    ok = passable.ravel()
    z = np.nan_to_num(grid.levels).ravel()
    steps = [
        (dj, di, max_rise * math.hypot(dj, di) * grid.cell + STEP_TOL)
        for dj in (-1, 0, 1)
        for di in (-1, 0, 1)
        if dj or di
    ]
    current = 0
    for start in np.flatnonzero(ok):
        if label[start] >= 0:
            continue
        label[start] = current
        queue = deque([int(start)])
        while queue:
            node = queue.popleft()
            j, i = divmod(node % plane, nx)
            height = z[node]
            for dj, di, tol in steps:
                nj, ni = j + dj, i + di
                if not (0 <= nj < ny and 0 <= ni < nx):
                    continue
                for nb in range(nj * nx + ni, layers * plane, plane):
                    if ok[nb] and label[nb] < 0 and abs(z[nb] - height) <= tol:
                        label[nb] = current
                        queue.append(nb)
        current += 1
    return label.reshape(passable.shape)
