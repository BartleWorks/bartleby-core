"""collision.bcd: the floor and the walls, read as the game stores them."""

import math

import numpy as np
import pytest

from bartleby.runtime.movement.collision import (
    CYLINDER,
    MESH,
    NON_MOVEMENT,
    OBJECT,
    SPHERE,
    Solid,
    blocking_footprints,
    object_rotation,
    parse_collision,
    parse_solid_xml,
    place_solids,
    placed_footprints,
    walkable_triangles,
)
from bartleby.runtime.movement.geometry import facing
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


# --- a placed object's solids ------------------------------------------------------------------

SOLID_FILE = """\ufeff<?xml version="1.0" encoding="utf-8"?>
<world>
  <primitive type="box" name="Collision Box" category="CT_ClientObject" material="Cobbles">
    <dimensions l="120.0" w="400.0" d="300.0" />
    <location x="10.0" y="0.0" z="150.0" />
    <rotation matrix="1.0 0.0 0.0 0.0 1.0 0.0 0.0 0.0 1.0" />
  </primitive>
  <primitive type="cylinder" name="PlayerClickCylinder_collision">
    <location x="0" y="0" z="100.0"/>
    <rotation matrix="1.0 0.0 0.0 0.0 1.0 0.0 0.0 0.0 1.0"/>
    <cylinder radius="43.0" length="210.0"/>
  </primitive>
  <primitive type="sphere" name="Collision sphere">
    <radius value="55.0" />
    <location x="0.0" y="0.0" z="20.0" />
    <rotation matrix="1.0 0.0 0.0 0.0 1.0 0.0 0.0 0.0 1.0" />
  </primitive>
  <primitive type="mesh" name="Box01">
    <mesh>
      <vertexlist size="4">
        <vert x="0" y="0" z="0"/><vert x="50" y="0" z="0"/><vert x="0" y="50" z="90"/><vert x="50" y="50" z="90"/>
      </vertexlist>
      <facelist size="2"><face a="0" b="1" c="2"/><face a="1" b="3" c="2"/></facelist>
    </mesh>
    <location x="0.0" y="0.0" z="0.0"/>
    <rotation matrix="1.0 0.0 0.0 0.0 1.0 0.0 0.0 0.0 1.0"/>
  </primitive>
  <primitive type="mesh" name="Elsewhere"><mesh source="other.nif"/></primitive>
</world>
""".encode()


def test_a_solid_file_reads_as_its_box_cylinder_sphere_and_mesh_and_skips_a_mesh_elsewhere():
    box_, cyl, ball, hull = parse_solid_xml(SOLID_FILE)

    assert (box_.proxy, box_.params, box_.location) == (0, (120.0, 400.0, 300.0), (10.0, 0.0, 150.0))
    assert (cyl.proxy, cyl.params) == (CYLINDER, (210.0, 43.0))  # (length, radius), as collision.bcd stores one
    assert (ball.proxy, ball.params) == (SPHERE, (55.0,))
    assert hull.proxy == MESH and len(hull.vertices) == 4 and len(hull.faces) == 2
    assert {s.category for s in (box_, cyl, ball, hull)} == {0}


def test_a_placed_solid_is_turned_by_its_yaw_scaled_and_moved():
    [solid] = parse_solid_xml(SOLID_FILE)[:1]

    [placed] = place_solids([solid], (1000.0, 2000.0, 5.0), (0.0, 0.0, math.pi / 2), 2.0)

    # Its own offset of 10 along x, doubled and turned a quarter by a yaw of pi / 2, is 20 along -y.
    assert placed.location == pytest.approx((1000.0, 1980.0, 305.0))
    assert placed.params == (240.0, 800.0, 600.0) and placed.scale == 1.0
    [footprint] = placed_footprints([placed])
    xs, ys = footprint.hull[:, 0], footprint.hull[:, 1]
    # A turned box's long side (400 along y, doubled) now lies along x.
    assert (xs.max() - xs.min(), ys.max() - ys.min()) == pytest.approx((800.0, 240.0))


def test_a_wizard_yaw_and_an_objects_turn_the_same_way():
    turn = object_rotation((0.0, 0.0, 0.7))
    # A wizard with yaw 0.7 walks along facing(0.7); an object's own -y (yaw 0's facing) turns onto it.
    assert tuple((turn @ np.array([0.0, -1.0, 0.0]))[:2]) == pytest.approx(facing(0.7))


def test_a_placed_solid_walls_the_wizard_whatever_its_category_and_a_mesh_by_its_hull():
    box_, cyl, ball, hull = parse_solid_xml(SOLID_FILE)
    client_object = Solid(box_.proxy, NON_MOVEMENT, box_.rotation, box_.location, 1.0, box_.params)

    footprints = placed_footprints([client_object, cyl, ball, hull])

    assert len(footprints) == 4
    mesh_print = footprints[-1]
    assert (mesh_print.zlo, mesh_print.zhi, mesh_print.firm) == (0.0, 90.0, True)
    assert blocking_footprints([client_object]) == []  # in a collision.bcd, a client object walls nobody
