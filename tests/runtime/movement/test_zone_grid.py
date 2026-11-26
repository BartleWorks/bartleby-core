"""The layered grid: every floor of a cell kept, clearance from walls and drops, pieces."""

import numpy as np
import pytest

from bartleby.runtime.movement.collision import blocking_footprints, walkable_triangles
from bartleby.runtime.movement.zone_grid import CELL, Grid, nearest_node, pieces
from tests.runtime.movement.zone_fixtures import box, floor, mesh


def two_storeys(stairs: bool = True):
    """A ground floor at z 0, x 0 to 3000, and a floor above it at z 400 over x 0 to 1000; a
    ramp along y 0 to 300 climbs from the ground at x 1500 to the upper floor at x 1000."""
    parts = [floor(0, 0, 3000, 1000, 0), floor(0, 0, 1000, 1000, 400)]
    if stairs:
        parts.append(floor(1000, 0, 1500, 300, 400, 0))
    return mesh(*parts)


def test_a_cell_keeps_every_floor_it_has():
    grid = Grid(walkable_triangles([two_storeys()]))

    j, i = grid.index(500, 500)
    assert len(grid.levels) == 2
    assert sorted(grid.levels[:, j, i]) == [0, 400]
    j, i = grid.index(2500, 500)
    assert grid.floors[j, i] == 1


def test_floor_near_a_wall_or_a_drop_is_not_passable():
    solids = [mesh(floor(0, 0, 2000, 2000, 0)), box((1000, 1000, 50), 200, 200, 100)]
    grid = Grid(walkable_triangles(solids))
    grid.add_walls(blocking_footprints(solids))
    passable = grid.passable()

    assert not passable[(0, *grid.index(1000, 1000))]  # the box
    assert not passable[(0, *grid.index(1000, 1130))]  # within 50 of it
    assert passable[(0, *grid.index(1000, 1300))]
    assert not passable[(0, *grid.index(1990, 1000))]  # the edge of the floor
    assert passable[(0, *grid.index(1900, 1000))]


def test_the_edge_of_an_upper_floor_is_a_drop():
    grid = Grid(walkable_triangles([two_storeys(stairs=False)]))
    passable = grid.passable()
    j, i = grid.index(990, 600)
    upper = int(np.nanargmax(grid.levels[:, j, i]))

    assert not passable[upper, j, i]
    assert passable[(upper, *grid.index(500, 600))]


def test_stairs_join_two_storeys_into_one_piece_and_nothing_else_does():
    with_stairs = Grid(walkable_triangles([two_storeys()]))
    without = Grid(walkable_triangles([two_storeys(stairs=False)]))

    def joined(grid: Grid) -> bool:
        passable = grid.passable()
        label = pieces(grid, passable)
        up = nearest_node(grid, passable, 500, 500, 400)
        down = nearest_node(grid, passable, 500, 500, 0)
        return label[up] == label[down]

    assert joined(with_stairs)
    assert not joined(without)


def test_a_point_snaps_to_the_floor_at_its_own_height():
    grid = Grid(walkable_triangles([two_storeys()]))
    passable = grid.passable()

    up = nearest_node(grid, passable, 500, 500, 390)
    down = nearest_node(grid, passable, 500, 500, 10)

    assert grid.world(*up)[2] == 400
    assert grid.world(*down)[2] == 0
    assert grid.world(*up)[:2] == grid.world(*down)[:2]


def test_nothing_passable_has_no_nearest_node():
    grid = Grid(walkable_triangles([mesh(floor(0, 0, 3 * CELL, 3 * CELL, 0))]))
    assert nearest_node(grid, np.zeros_like(grid.levels, dtype=bool), 0, 0) is None


def test_a_zone_with_no_floor_covering_a_cell_says_so():
    # The Dragonspyre dragon's flight path: nothing in it is ground a wizard stands on.
    sliver = np.array([[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]])

    with pytest.raises(ValueError, match="nothing to walk on"):
        Grid(sliver)
