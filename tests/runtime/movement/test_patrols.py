"""Patrols from the spawn files, and what passing near one costs."""

import numpy as np
import pytest

from bartleby.runtime.movement.collision import walkable_triangles
from bartleby.runtime.movement.patrols import (
    HARD,
    HARD_WEIGHT,
    SOFT,
    Patrol,
    clearance,
    danger_cost,
    distance_to_segments,
    mob_patrols,
    segments,
)
from bartleby.runtime.movement.zone_grid import Grid
from tests.runtime.movement.zone_fixtures import floor, mesh

GHOST, CHEST = 11, 22


def _duelist(template_id: int):
    return ("Ghost", 200.0) if template_id == GHOST else None


def _spawner(name: str, *entries: tuple[int, int, int]) -> dict:
    return {
        "m_name": name,
        "m_maxNumberOfSpawns": 2,
        "m_spawnList": [
            {"m_objectInfo": {"m_templateID.m_full": t, "m_kStartNodeType": kind, "m_pathID": path}}
            for t, kind, path in entries
        ],
    }


PATHS = {1: [10, 11, 12], 2: [20]}
NODES = {10: (0, 0, 0), 11: (1000, 0, 0), 12: (1000, 1000, 0), 20: (5, 5, 0)}


def test_a_duelist_spawned_on_a_path_patrols_it_in_path_order():
    [patrol] = mob_patrols([_spawner("Ghosts 01", (GHOST, 0, 1))], PATHS, NODES, _duelist)

    assert (patrol.spawner, patrol.template, patrol.proximity, patrol.max_spawns) == ("Ghosts 01", "Ghost", 200, 2)
    assert patrol.points.tolist() == [[0, 0, 0], [1000, 0, 0], [1000, 1000, 0]]


def test_a_spawner_that_is_no_duelist_or_on_no_known_path_is_no_patrol():
    spawners = [_spawner("Chests", (CHEST, 0, 1)), _spawner("Lost", (GHOST, 0, 99)), _spawner("Empty")]
    assert mob_patrols(spawners, PATHS, NODES, _duelist) == []


def test_both_start_node_types_walk_their_paths_unless_told_otherwise():
    spawners = [_spawner("A", (GHOST, 0, 1)), _spawner("B", (GHOST, 1, 2))]

    assert [p.start_node_type for p in mob_patrols(spawners, PATHS, NODES, _duelist)] == [0, 1]
    assert [p.spawner for p in mob_patrols(spawners, PATHS, NODES, _duelist, starts=(0,))] == ["A"]


def test_a_spawner_gives_one_patrol_whatever_it_rolls():
    spawners = [_spawner("Ghosts", (CHEST, 0, 1), (GHOST, 0, 1), (GHOST, 1, 2))]
    [patrol] = mob_patrols(spawners, PATHS, NODES, _duelist)
    assert patrol.start_node_type == 0


def test_a_path_loops_back_to_its_first_node():
    patrol = Patrol("p", "t", 200, 1, np.array([(0, 0, 0), (100, 0, 0), (100, 100, 0)], dtype=float))
    assert len(segments(patrol, closed=True)) == 3
    assert len(segments(patrol, closed=False)) == 2


def test_distance_to_segments_is_to_the_nearest_point_of_any():
    segs = np.array([[[0, 0], [100, 0]]], dtype=float)
    d = distance_to_segments(np.array([50.0, 150.0]), np.array([30.0, 0.0]), segs)
    assert d.tolist() == pytest.approx([30, 50])


def test_clearance_is_the_room_beyond_each_mobs_own_trigger_radius():
    grid = Grid(walkable_triangles([mesh(floor(0, 0, 4000, 400, 0))]))
    ghost = Patrol("g", "Ghost", 200, 1, np.array([(1000, 200, 0)], dtype=float))
    sprite = Patrol("s", "Sprite", 275, 1, np.array([(3000, 200, 0)], dtype=float))
    room = clearance(grid, [ghost, sprite])

    j, i = grid.index(1500, 200)
    x, y, _ = grid.world(0, j, i)
    assert room[j, i] == pytest.approx(np.hypot(x - 1000, y - 200) - 200)
    j, i = grid.index(2600, 200)
    x, y, _ = grid.world(0, j, i)
    assert room[j, i] == pytest.approx(np.hypot(x - 3000, y - 200) - 275)


def test_no_patrols_leave_every_cell_infinite_room():
    grid = Grid(walkable_triangles([mesh(floor(0, 0, 400, 400, 0))]))
    assert np.isinf(clearance(grid, [])).all()


def test_the_cost_is_nil_beyond_soft_rising_toward_hard_and_steep_inside_it():
    cost = danger_cost(np.array([SOFT + 1, (SOFT + HARD) / 2, HARD + 1, HARD - 1]))
    assert cost[0] == 1
    assert 1 < cost[1] < cost[2] < cost[3]
    assert cost[3] == 1 + HARD_WEIGHT
