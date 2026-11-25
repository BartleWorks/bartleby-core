"""collision.bcd: the floor and the walls, read as the game stores them."""

import numpy as np
import pytest

from bartleby.runtime.movement.collision import (
    CYLINDER,
    MESH,
    NON_MOVEMENT,
    OBJECT,
    SPHERE,
    blocking_footprints,
    parse_collision,
    walkable_triangles,
)
from bartleby.runtime.movement.zone_grid import Grid
from tests.runtime.movement.zone_fixtures import box, collision_bytes, cylinder, floor, mesh, sphere


def test_a_collision_file_parses_back_to_its_solids():
    solids = [mesh(floor(0, 0, 400, 400, 0)), box((200, 200, 50), 100, 60, 100), cylinder((50, 50, 40), 80, 30)]

    parsed = parse_collision(collision_bytes(solids))

    assert [s.proxy for s in parsed] == [MESH, 0, CYLINDER]
    assert parsed[0].faces == [(0, 1, 2), (3, 4, 5)]
    assert parsed[1].params == pytest.approx((100, 60, 100))
    assert parsed[2].location == pytest.approx((50, 50, 40))


def test_floor_is_the_walkable_meshes_less_their_cliffs():
    wall = [(0, 0, 0), (0, 100, 0), (0, 0, 300)]  # a vertical face
    unwalkable = mesh(floor(1000, 0, 1100, 100, 0), category=0)

    tris = walkable_triangles([mesh(floor(0, 0, 400, 400, 0), wall), unwalkable])

    assert len(tris) == 2
    assert np.all(tris[:, :, 2] == 0)


def test_a_cylinder_is_read_as_length_then_radius():
    # The Unicorn Way tree: stored (264.9, 85.7), a wizard held 135 from its center, radius 85.7
    # plus its own 49. Read the other way it is a 265-wide disc.
    [tree] = blocking_footprints([cylinder((0, 0, 132), 264.9, 85.7)])

    assert tree.radius == pytest.approx(85.7)
    assert (tree.zlo, tree.zhi) == pytest.approx((132 - 132.45, 132 + 132.45))


def test_triggers_and_fog_are_no_wall_but_a_flagged_object_is():
    trigger = sphere((0, 0, 0), 100, category=1 << 8)
    flagged_object = sphere((0, 0, 0), 100, category=(1 << 8) | OBJECT)

    assert blocking_footprints([trigger]) == []
    assert len(blocking_footprints([flagged_object])) == 1
    assert NON_MOVEMENT & (1 << 8)


def _walled(solid) -> bool:
    """Whether *solid* walls the floor at its foot, on a flat street at z 0."""
    grid = Grid(walkable_triangles([mesh(floor(-1000, -1000, 1000, 1000, 0))]))
    grid.add_walls(blocking_footprints([solid]))
    j, i = grid.index(solid.location[0], solid.location[1])
    return bool(grid.walls[0, j, i])


def test_a_round_solid_under_radius_400_walls_once_it_rises_20():
    # Mirage's rock: a cylinder of radius 143 rising 56 above the street walls it.
    assert _walled(cylinder((0, 0, 0), 112, 143))  # from -56 to 56
    assert not _walled(cylinder((0, 0, 0), 30, 143))  # rises 15


def test_a_round_solid_of_radius_400_or_more_walls_only_once_it_rises_75():
    # A big round is often the coarse bounds of a structure whose floor lies inside it.
    assert not _walled(cylinder((0, 0, 0), 112, 450))  # rises 56
    assert _walled(cylinder((0, 0, 0), 160, 450))  # rises 80


def test_a_box_walls_once_it_rises_20():
    assert _walled(box((0, 0, 0), 200, 200, 60))  # rises 30
    assert not _walled(box((0, 0, 0), 200, 200, 30))  # rises 15


def test_a_solid_overhead_walls_the_floor_under_it_but_not_one_far_above():
    assert _walled(box((0, 0, 100), 200, 200, 120))  # base at 40: a low beam
    assert not _walled(box((0, 0, 400), 200, 200, 100))  # base at 350: a bridge


def test_a_sphere_footprint_is_its_radius_times_its_scale():
    ball = sphere((0, 0, 0), 50)
    ball.scale = 2.0
    [print_] = blocking_footprints([ball])
    assert print_.radius == 100
    assert ball.proxy == SPHERE
