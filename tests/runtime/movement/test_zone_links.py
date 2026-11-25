"""In-zone links and exits: the files they are read from, and each way a landing is found."""

import math

import pytest

from bartleby.runtime.movement.collision import walkable_triangles
from bartleby.runtime.movement.zone_grid import Grid, pieces
from bartleby.runtime.movement.zone_links import (
    InteractTemplate,
    Link,
    PlacedObject,
    Volume,
    ZoneNav,
    interact_candidates,
    link_candidates,
    parse_interact_template,
    parse_volumes,
    parse_zone_nav,
    teleport_volumes,
    zone_exits,
    zone_links,
)
from tests.runtime.movement.zone_fixtures import (
    floor,
    mesh,
    template_bytes,
    triggers_bytes,
    volumes_bytes,
    zone_nav_bytes,
)

ZONE = "Test/T_Street"
TELEPORT = InteractTemplate("Teleporter", "Press X to Teleport")


# --- the files -----------------------------------------------------------------------------


def test_a_volume_with_no_shape_is_a_sphere_of_its_radius():
    [hub] = parse_volumes(volumes_bytes([("TeleportVol_Hub Exit", (-9, -1148, 18), None, 357.7, 0, 0, 0)]))

    assert (hub.name, hub.shape) == ("TeleportVol_Hub Exit", "Sphere")
    assert hub.center == pytest.approx((-9, -1148, 18))
    assert hub.reach == pytest.approx(357.7)
    assert hub.rise == pytest.approx(357.7)


def test_a_box_volume_reaches_as_far_as_its_corners_however_it_is_turned():
    [gate] = parse_volumes(volumes_bytes([("Exit_Volume", (0, 0, 0), "Box", 0, 600, 800, 400)]))

    assert gate.shape == "Box"
    assert gate.reach == pytest.approx(500)  # half the 600 by 800 diagonal
    assert gate.rise == pytest.approx(400)


def test_each_volume_keeps_its_own_size():
    rows = [("Gate A", (0, 0, 0), None, 100, 0, 0, 0), ("Gate B", (500, 0, 0), "Sphere", 200, 0, 0, 0)]
    assert [(v.name, v.radius) for v in parse_volumes(volumes_bytes(rows))] == [("Gate A", 100), ("Gate B", 200)]


def test_a_volume_is_a_teleport_when_a_teleporting_trigger_fires_on_entering_it():
    data = triggers_bytes(
        [
            ("Teleport To The Tower", ["Enter_Activator Volume (2)"], True),
            ("UnicornParkPOITrigger", ["Enter_UnicornParkPOI"], False),
            ("Trigger-HideSigil", ["Exit_Gate Volume"], True),
            ("Combat_Music (1)", ["Enter_Combat_Music"], False),
        ]
    )
    assert teleport_volumes(data) == {"Activator Volume (2)"}


def test_zone_nav_parses_to_its_nodes_and_edges():
    nav = parse_zone_nav(zone_nav_bytes([(0, 0, 0), (480, 0, 0), (6000, 0, 0)], [(0, 1), (1, 0), (1, 2)]))

    assert nav.nodes[2] == (6000, 0, 0)
    assert nav.pairs == [(0, 1), (1, 0), (1, 2)]
    assert nav.link_edges() == [(1, 2)]


def test_zone_nav_takes_a_steep_climb_for_a_link():
    nav = ZoneNav([(0, 0, 0), (200, 0, 1100), (480, 0, 100)], [(0, 1), (0, 2)])
    assert nav.link_edges() == [(0, 1)]


def test_zone_nav_whose_sizes_do_not_add_up_is_refused():
    with pytest.raises(ValueError, match="left over"):
        parse_zone_nav(zone_nav_bytes([(0, 0, 0), (1, 1, 1)], [(0, 1)]) + b"\x00")
    with pytest.raises(ValueError, match="out of range"):
        parse_zone_nav(zone_nav_bytes([(0, 0, 0), (1, 1, 1)], [(0, 5)]))


def test_a_template_gives_its_prompt_and_its_location_records():
    data = template_bytes("GUI_00000042", [((1, 2, 3), ZONE), ((7000, 0, 50), ZONE)])
    lang = {("GUI", "00000042"): "Press X to Ride"}.get

    template = parse_interact_template("ZipLine", data, lambda stem, key: lang((stem, key), ""))

    assert template.prompt == "Press X to Ride"
    assert [zone for _, zone in template.ends] == [ZONE, ZONE]
    assert template.ends[1][0] == pytest.approx((7000, 0, 50))


def test_a_template_with_no_interactable_behavior_is_none():
    assert parse_interact_template("Rock", b"BINd" + bytes(40), lambda stem, key: "") is None


# --- landings ------------------------------------------------------------------------------


def _volume(name: str, at: tuple) -> Volume:
    return Volume(name, at, radius=90)


def test_a_volume_lands_where_its_name_matches_to_and_from_breaking_a_tie():
    named = {"From_Kraken_Isle": (100, 0, 0), "To_Kraken_Isle": (5000, 0, 0)}
    [link] = link_candidates([_volume("TeleportVol From Kraken Isle", (4900, 0, 0))], named)
    assert link.landing == "From_Kraken_Isle"


def test_a_volume_two_landings_fit_as_well_is_no_link():
    named = {"Isle East": (100, 0, 0), "Isle West": (5000, 0, 0)}
    assert link_candidates([_volume("TeleportVol Isle", (0, 0, 0))], named) == []


def test_a_volume_that_does_not_say_it_moves_is_no_link_candidate():
    named = {"Park": (0, 0, 0)}
    assert link_candidates([_volume("ParkPOI", (0, 0, 0)), _volume("Teleport Cinematic Park", (0, 0, 0))], named) == []


def test_a_ride_lands_on_the_arrival_point_past_its_far_end():
    ride = InteractTemplate("ZipLine-3", "Press X to Ride", (((0, 100, 0), ZONE), ((8000, 0, 0), ZONE)))
    named = {"Target-ToMajorIsland": (8800, 0, 0), "Start": (0, 0, 0)}

    [link] = interact_candidates(ZONE, [PlacedObject("AZ-ZipLine-3", (0, 0, 0), ride)], named, None)

    assert (link.kind, link.landing) == ("ride", "Target-ToMajorIsland")


def test_a_stone_lands_by_its_partner():
    stone = InteractTemplate("Stone", "")
    placed = [
        PlacedObject("WC_Triton_TeleporterA instance", (100, 100, 0), stone),
        PlacedObject("WC_Triton_TeleporterA_ToMain", (5000, 0, 0), stone),
    ]
    named = {"StreetA": (5050, 0, 0), "Hub": (150, 100, 0)}

    links = {link.name: link.landing for link in interact_candidates(ZONE, placed, named, None)}

    assert links == {"WC_Triton_TeleporterA instance": "StreetA", "WC_Triton_TeleporterA_ToMain": "Hub"}


def test_an_x_and_x_b_pair_each_land_by_the_other():
    placed = [
        PlacedObject("Tomb_Teleporter", (0, 0, 0), TELEPORT),
        PlacedObject("Tomb_Teleporter_B", (6000, 0, 0), TELEPORT),
    ]
    named = {"TombNear": (100, 0, 0), "TombFar": (6100, 0, 0)}

    links = {link.name: link.landing for link in interact_candidates(ZONE, placed, named, None)}

    assert links == {"Tomb_Teleporter": "TombFar", "Tomb_Teleporter_B": "TombNear"}


def test_a_travel_object_lands_where_its_zone_nav_edge_ends():
    ladder = InteractTemplate("Ladder", "Climb")
    nav = ZoneNav([(50, 0, 0), (60, 0, 800)], [(0, 1), (1, 0)])
    named = {"Ladder 01 top": (70, 0, 810), "Ladder 01 foot": (0, 0, 0)}

    [link] = interact_candidates(ZONE, [PlacedObject("Ladder 01", (0, 0, 0), ladder)], named, nav)

    assert (link.kind, link.landing) == ("interact", "Ladder 01 top")


def test_an_object_that_is_not_for_travel_is_no_link_but_a_named_rope_with_no_prompt_is():
    nav = ZoneNav([(0, 0, 0), (0, 0, 900)], [(0, 1)])
    named = {"Top": (0, 0, 900)}
    chair = PlacedObject("Chair", (0, 0, 0), InteractTemplate("Chair", "Sit"))
    rope = PlacedObject("Rope", (0, 0, 0), InteractTemplate("ClimbingRope", ""))

    assert interact_candidates(ZONE, [chair], named, nav) == []
    assert [link.name for link in interact_candidates(ZONE, [rope], named, nav)] == ["Rope"]


def _islands():
    """Two pieces of floor no walk joins, 2000 apart."""
    grid = Grid(walkable_triangles([mesh(floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0))]))
    passable = grid.passable()
    return grid, passable, pieces(grid, passable)


def test_a_volume_link_joins_two_pieces_of_floor_and_an_exit_on_its_own_piece_is_none():
    grid, passable, label = _islands()
    volumes = [_volume("TeleportVol To Isle", (500, 500, 0)), _volume("TeleportVol Street to Cavern", (200, 800, 0))]
    named = {"Target To Isle": (3500, 500, 0), "Target From Isle": (300, 300, 0), "Cavern to Street": (300, 700, 0)}

    links = zone_links(ZONE, grid, passable, label, volumes, [], named, None)

    assert [(link.name, link.landing) for link in links] == [("TeleportVol To Isle", "Target To Isle")]


def test_an_object_at_a_volume_is_the_pad_drawn_there():
    grid, passable, label = _islands()
    volumes = [_volume("TeleportVol To Isle", (500, 500, 0))]
    named = {"Target To Isle": (3500, 500, 0)}
    pad = [PlacedObject("Telepapad", (520, 500, 0), TELEPORT), PlacedObject("Telepapad_B", (3500, 400, 0), TELEPORT)]

    links = zone_links(ZONE, grid, passable, label, volumes, pad, named, None)

    assert [link.name for link in links] == ["TeleportVol To Isle"]


def test_an_object_on_one_piece_is_kept_only_as_a_long_shortcut():
    grid = Grid(walkable_triangles([mesh(floor(0, 0, 4000, 1000, 0))]))
    passable = grid.passable()
    label = pieces(grid, passable)
    placed = [
        PlacedObject("Near", (500, 500, 0), TELEPORT),
        PlacedObject("Near_B", (900, 500, 0), TELEPORT),
        PlacedObject("Far", (200, 200, 0), TELEPORT),
        PlacedObject("Far_B", (3800, 200, 0), TELEPORT),
    ]
    named = {"by near": (560, 500, 0), "by near b": (860, 500, 0), "by far": (260, 200, 0), "by far b": (3740, 200, 0)}

    links = zone_links(ZONE, grid, passable, label, [], placed, named, None)

    assert sorted(link.name for link in links) == ["Far", "Far_B"]


def test_a_zones_exits_are_its_teleport_volumes_that_are_not_its_links():
    gate, isle, park = (
        _volume("Gate", (0, 0, 0)),
        _volume("TeleportVol To Isle", (500, 0, 0)),
        _volume("Park", (9, 9, 0)),
    )
    link = Link(isle.name, isle.center, "Target To Isle", (3500, 0, 0))

    assert zone_exits([gate, isle, park], frozenset({"Gate", "TeleportVol To Isle"}), [link]) == [gate]


def test_a_link_costs_seconds_by_its_kind_and_is_taken_nearer_a_volume_than_an_object():
    volume, press, ride = (Link("v", (0, 0, 0), "a", (1, 1, 1), kind) for kind in ("volume", "interact", "ride"))
    assert (volume.seconds, press.seconds, ride.seconds) == (4, 6, 20)
    assert volume.reach < press.reach
    assert math.isclose(ride.reach, press.reach)
