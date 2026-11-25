"""A zone's gates: where a placed object's solids stand, which are a link's and so walls, and
how a missing route is explained."""

import math

import numpy as np
import pytest

from bartleby.runtime.movement.collision import Footprint
from bartleby.runtime.movement.gates import Crossing, Gate, NoRoute, mark_walls
from bartleby.runtime.movement.zone_links import Link

SQUARE = np.array([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0)])


def gate(name="Door", at=(50.0, 50.0, 0.0), *footprints, wall=False) -> Gate:
    return Gate(name, "Door", at, footprints or (Footprint(SQUARE, (0.0, 0.0), 0.0, 0.0, 300.0, True),), wall)


def test_a_point_inside_a_gates_solid_is_no_distance_from_it():
    assert gate().distance(50, 50, 0) == 0.0


def test_a_point_beside_a_gates_solid_is_its_distance_from_the_nearest_side():
    assert gate().distance(130, 50, 0) == pytest.approx(30.0)
    assert gate().distance(130, 140, 0) == pytest.approx(50.0)  # from the corner (100, 100)


def test_a_round_solids_distance_is_from_its_rim():
    round_ = gate("Post", (0, 0, 0), Footprint(None, (0.0, 0.0), 40.0, 0.0, 200.0, True))
    assert round_.distance(100, 0, 0) == pytest.approx(60.0)


def test_a_gate_on_another_storey_is_not_near():
    assert gate().distance(50, 50, 800) == math.inf


def test_a_gate_where_a_pressed_link_is_taken_is_a_wall_and_no_other_is():
    stone = gate("Stone_A", (500.0, 500.0, 0.0))
    other = gate("Stone_A", (900.0, 500.0, 0.0))
    stepped_in = gate("Vol_B", (100.0, 100.0, 0.0))
    links = [
        Link("Stone_A", (500.0, 500.0, 0.0), "There", (4000.0, 0.0, 0.0), "interact"),
        Link("Vol_B", (100.0, 100.0, 0.0), "Here", (0.0, 4000.0, 0.0), "volume"),
    ]

    marked = mark_walls([stone, other, stepped_in], links)

    assert [g.wall for g in marked] == [True, False, False]


def test_a_gates_key_holds_everything_it_holds():
    assert gate().key() == gate().key()
    assert gate().key() != gate(wall=True).key()
    assert gate().key() != gate("Door", (50.0, 50.0, 0.0), Footprint(None, (0.0, 0.0), 9.0, 0.0, 1.0, True)).key()


def test_no_route_names_the_gate_in_the_way_and_the_crossing_past_it():
    why = NoRoute("Test/T", (0, 0, 0), (900, 0, 0), "Door", Crossing((380.0, 500.0, 0.0), (620.0, 500.0, 0.0), 240.0))

    assert why.describe() == "the closed gate Door is in the way: 240 units past it, from (380, 500) to (620, 500)"
    assert NoRoute("Test/T", (0, 0, 0), (900, 0, 0)).describe() == "nowhere on the floor joins the two"
