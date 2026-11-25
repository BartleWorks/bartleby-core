"""The shipped zone maps: what a map holds, that it plans as its wad does, and the saved copies."""

import ast
import hashlib
import inspect
import io
import json
import logging
import math
import struct
import threading
import tokenize
import zipfile
import zlib
from pathlib import Path

import numpy as np
import pytest

from bartleby.runtime.movement import collision, patrols, route, zone_grid, zone_maps
from bartleby.runtime.movement.collision import Footprint, blocking_footprints, walkable_triangles
from bartleby.runtime.movement.gates import Gate
from bartleby.runtime.movement.patrols import Patrol
from bartleby.runtime.movement.route import ZoneFiles, build_planner
from bartleby.runtime.movement.zone_links import InteractTemplate, PlacedObject, Volume
from bartleby.runtime.movement.zone_maps import ZoneMaps
from tests.runtime.movement.zone_fixtures import box, cylinder, floor, mesh
from tools.build_zone_maps import build_group

ZONE = "Test/T_Isles"
TWIN = "Test/T_Isles_Twin"


def _isles(zone: str = ZONE) -> ZoneFiles:
    """Two islands joined by a volume, a ghost walking the west one, an exit on the east one,
    a crate and a tree to walk around, and a quest light, a gate, on the east one."""
    solids = [
        mesh(floor(0, 0, 2000, 1000, 0), floor(3000, 0, 5000, 1000, 0)),
        box((1000, 800, 50), 200, 200, 200),
        cylinder((4000, 300, 100), 200, 60),
    ]
    ghost = Patrol("Ghosts 01", "Ghost", 200, 2, np.array([(600, 100, 0), (600, 900, 0)], dtype=float), 1)
    link = Volume("TeleportVol To Isle", (1800, 500, 0), radius=90)
    gate = Volume("TeleportVol_Gate", (4600, 500, 0), shape="Box", length=200, width=120, height=300)
    named = {"Start": (100.5, 500.25, 0.0), "Target To Isle": (3200, 500, 0), "Far End": (4900, 900, 0)}
    light = Gate(
        "Quest Light 01",
        "QuestLight",
        (3600.0, 700.0, 0.0),
        (Footprint(None, (3600.0, 700.0), 60.0, 0.0, 200.0, True),),
    )
    return ZoneFiles(zone, solids, [ghost], named, [link, gate], frozenset({link.name, gate.name}), gates=[light])


def _ship(root: Path, *group: ZoneFiles) -> None:
    """Write *group*, zones sharing one floor, under *root* as the build tool does."""
    floor_bytes, texts = build_group(group)
    key = zone_maps.floor_key(floor_bytes)
    (root / zone_maps.FLOORS).mkdir(parents=True, exist_ok=True)
    (root / zone_maps.ZONES).mkdir(parents=True, exist_ok=True)
    (root / zone_maps.FLOORS / f"{key}.npz").write_bytes(floor_bytes)
    index = {}
    for zone, text in texts.items():
        (root / zone_maps.ZONES / zone_maps.zone_file_name(zone)).write_text(text, encoding="utf-8")
        index[zone] = {
            "floor": f"{zone_maps.FLOORS}/{key}.npz",
            "zone": f"{zone_maps.ZONES}/{zone_maps.zone_file_name(zone)}",
            "wad": "0" * 64,
        }
    (root / zone_maps.INDEX_NAME).write_text(zone_maps.index_text(index), encoding="utf-8")


@pytest.fixture
def shipped(tmp_path) -> Path:
    root = tmp_path / "maps"
    _ship(root, _isles(), _isles(TWIN))
    return root


def _maps(shipped: Path, tmp_path: Path) -> ZoneMaps:
    return ZoneMaps(shipped, tmp_path / "saved")


def _assert_plans_alike(one, other) -> None:
    np.testing.assert_array_equal(one.grid.levels, other.grid.levels)
    np.testing.assert_array_equal(one.grid.walls, other.grid.walls)
    assert (one.grid.x0, one.grid.y0, one.grid.cell) == (other.grid.x0, other.grid.y0, other.grid.cell)
    np.testing.assert_array_equal(one.passable, other.passable)
    np.testing.assert_array_equal(one.clearance, other.clearance)
    np.testing.assert_array_equal(one.cost, other.cost)
    assert (one.named, one.links, one.exits) == (other.named, other.links, other.exits)
    assert [g.key() for g in one.gates] == [g.key() for g in other.gates]
    for start, goal in [((100, 500), (4900, 900)), ((300, 200), (1500, 600)), ((3200, 300), (4900, 300))]:
        for avoid in (False, True):
            a, b = one.route(start, goal, avoid), other.route(start, goal, avoid)
            assert a is not None and b is not None
            assert a.nodes == b.nodes
            assert one.legs(a) == other.legs(b)


# --- what a map holds ------------------------------------------------------------------------


def test_a_floor_reads_back_as_the_triangles_and_footprints_written():
    files = _isles()
    triangles, footprints = walkable_triangles(files.solids), blocking_footprints(files.solids)

    read_triangles, read_footprints = zone_maps.read_floor(zone_maps.floor_bytes(triangles, footprints))

    np.testing.assert_array_equal(read_triangles, triangles)
    assert len(read_footprints) == len(footprints) == 2
    for read, made in zip(read_footprints, footprints):
        assert (read.center, read.radius, read.zlo, read.zhi, read.firm) == (
            made.center,
            made.radius,
            made.zlo,
            made.zhi,
            made.firm,
        )
        assert (read.hull is None) == (made.hull is None)
        if made.hull is not None:
            np.testing.assert_array_equal(read.hull, made.hull)


def test_a_zones_own_file_reads_back_as_written():
    planner = build_planner(_isles())

    text = zone_maps.zone_text(ZONE, planner.patrols, planner.named, planner.links, planner.exits, planner.gates)
    record = zone_maps.read_zone(text)

    assert [g.key() for g in record.gates] == [g.key() for g in planner.gates] and record.gates

    assert (record.zone, record.named, record.links, record.exits) == (
        ZONE,
        planner.named,
        planner.links,
        planner.exits,
    )
    [ghost] = record.patrols
    assert (ghost.spawner, ghost.template, ghost.proximity, ghost.max_spawns, ghost.start_node_type) == (
        "Ghosts 01",
        "Ghost",
        200,
        2,
        1,
    )
    np.testing.assert_array_equal(ghost.points, planner.patrols[0].points)


def test_a_map_holds_derived_geometry_names_and_positions_only(shipped):
    """No pickled object, no game file: plain arrays, and JSON of names and numbers."""
    index = json.loads((shipped / zone_maps.INDEX_NAME).read_text(encoding="utf-8"))
    entry = index["zones"][ZONE]
    with np.load(shipped / entry["floor"], allow_pickle=False) as arrays:
        assert sorted(arrays.files) == ["footprints", "hull_sizes", "hulls", "triangles"]
        assert all(arrays[name].dtype != object for name in arrays.files)
    record = json.loads((shipped / entry["zone"]).read_text(encoding="utf-8"))
    assert sorted(record) == ["exits", "format", "gates", "links", "named", "patrols", "zone"]


def test_zones_sharing_a_collision_file_share_one_floor_file(shipped):
    index = ZoneMaps(shipped).index
    assert index[ZONE]["floor"] == index[TWIN]["floor"]
    assert index[ZONE]["zone"] != index[TWIN]["zone"]
    assert len(list((shipped / zone_maps.FLOORS).iterdir())) == 1


def test_a_file_of_another_format_is_refused(shipped, tmp_path):
    planner = build_planner(_isles())
    record = json.loads(zone_maps.zone_text(ZONE, [], planner.named, planner.links, planner.exits))
    with pytest.raises(ValueError, match="format"):
        zone_maps.read_zone(json.dumps(record | {"format": zone_maps.FORMAT + 1}))

    (shipped / zone_maps.INDEX_NAME).write_text(json.dumps({"format": 0, "zones": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="format"):
        _maps(shipped, tmp_path).has(ZONE)


# --- has and load ------------------------------------------------------------------------------


def test_has_answers_from_the_index_without_opening_a_map(shipped, tmp_path):
    for folder in (zone_maps.FLOORS, zone_maps.ZONES):
        for path in (shipped / folder).iterdir():
            path.unlink()
    maps = _maps(shipped, tmp_path)

    assert maps.has(ZONE) and maps.has(TWIN)
    assert not maps.has("Test/T_Elsewhere")
    assert maps.zones() == [ZONE, TWIN]
    with pytest.raises(FileNotFoundError):
        maps.planner(ZONE)


def test_with_no_index_there_is_no_map(tmp_path):
    assert not ZoneMaps(tmp_path / "none", tmp_path / "saved").has(ZONE)


def test_a_planner_from_a_map_plans_as_one_from_the_wad(shipped, tmp_path):
    _assert_plans_alike(_maps(shipped, tmp_path).planner(ZONE), build_planner(_isles()))


def test_a_planner_from_its_saved_copy_plans_as_one_from_the_wad(shipped, tmp_path, monkeypatch):
    _maps(shipped, tmp_path).planner(ZONE)

    def no_building(*_args):
        raise AssertionError("built again, though a copy was saved")

    monkeypatch.setattr(zone_maps, "clearance", no_building)
    monkeypatch.setattr(zone_maps, "grid_of", no_building)
    _assert_plans_alike(_maps(shipped, tmp_path).planner(ZONE), build_planner(_isles()))


async def test_load_reads_a_planner_off_the_event_loop(shipped, tmp_path, monkeypatch):
    loop_thread = threading.get_ident()
    threads = []
    real = ZoneMaps.planner

    def planner(self, zone):
        threads.append(threading.get_ident())
        return real(self, zone)

    monkeypatch.setattr(ZoneMaps, "planner", planner)
    planner_ = await _maps(shipped, tmp_path).load(ZONE)

    assert planner_.zone == ZONE
    assert threads and loop_thread not in threads


def test_a_link_objects_wall_is_built_from_the_map_as_from_the_wad(tmp_path):
    teleport = InteractTemplate("Stone", "Press X to Teleport")
    stone = Gate("Stone_A", "Stone", (500.0, 500.0, 0.0), (Footprint(None, (500.0, 500.0), 43.0, 0.0, 210.0, True),))
    files = ZoneFiles(
        ZONE,
        [mesh(floor(0, 0, 1000, 1000, 0), floor(3000, 0, 4000, 1000, 0))],
        [],
        {"Near": (600.0, 500.0, 0.0), "Far": (3400.0, 500.0, 0.0)},
        placed=[PlacedObject("Stone_A", (500, 500, 0), teleport), PlacedObject("Stone_A_B", (3500, 500, 0), teleport)],
        gates=[stone],
    )
    _ship(tmp_path / "maps", files)
    wad, shipped = build_planner(files), ZoneMaps(tmp_path / "maps", tmp_path / "saved").planner(ZONE)

    assert shipped.gates[0].wall and shipped.grid.walls[(0, *shipped.grid.index(500, 500))]
    np.testing.assert_array_equal(wad.grid.walls, shipped.grid.walls)
    np.testing.assert_array_equal(wad.passable, shipped.passable)
    assert wad.links == shipped.links


def test_each_caller_gets_its_own_floor_and_links(shipped, tmp_path):
    maps = _maps(shipped, tmp_path)
    one, other = maps.planner(ZONE), maps.planner(ZONE)

    one.block(300, 500, 0, 200)
    one.drop(one.links[0])
    one.close(one.gates[0])

    assert other.passable.sum() > one.passable.sum()
    assert other.links and not one.links
    assert maps.planner(ZONE).links == other.links
    assert one.closed and other.closed == [] and maps.planner(ZONE).closed == []


def test_a_session_keeps_only_the_planners_used_last(shipped, tmp_path, monkeypatch):
    monkeypatch.setattr(zone_maps, "KEPT", 1)
    maps = _maps(shipped, tmp_path)
    reads = []
    real = maps._read
    monkeypatch.setattr(maps, "_read", lambda zone: reads.append(zone) or real(zone))

    maps.planner(ZONE)
    maps.planner(ZONE)
    maps.planner(TWIN)
    maps.planner(ZONE)

    assert reads == [ZONE, TWIN, ZONE]


# --- the saved copies --------------------------------------------------------------------------


def test_the_saved_copies_live_in_the_vendors_local_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert zone_maps.cache_dir() == tmp_path / "Bartleby" / "cache" / "zone_maps"
    assert ZoneMaps(tmp_path).cache == zone_maps.cache_dir()


def test_a_first_load_saves_a_copy_and_a_second_reads_it(shipped, tmp_path):
    maps = _maps(shipped, tmp_path)
    assert not maps.saved(ZONE)

    maps.planner(ZONE)

    assert maps.saved(ZONE)
    assert [p.name for p in (tmp_path / "saved").iterdir()] == ["Test-T_Isles.npz"]


def test_a_copy_saved_from_another_map_is_rebuilt(shipped, tmp_path):
    _maps(shipped, tmp_path).planner(ZONE)
    own = shipped / ZoneMaps(shipped).index[ZONE]["zone"]
    record = json.loads(own.read_text(encoding="utf-8"))
    record["patrols"] = []
    own.write_text(json.dumps(record), encoding="utf-8")
    maps = _maps(shipped, tmp_path)

    assert not maps.saved(ZONE)
    assert math.isinf(maps.planner(ZONE).clearance.min())
    assert maps.saved(ZONE)


def test_a_copy_saved_by_another_cache_version_is_rebuilt(shipped, tmp_path, monkeypatch):
    _maps(shipped, tmp_path).planner(ZONE)
    monkeypatch.setattr(zone_maps, "CACHE_VERSION", zone_maps.CACHE_VERSION + 1)
    assert not _maps(shipped, tmp_path).saved(ZONE)


def test_a_damaged_copy_is_rebuilt_and_said_so(shipped, tmp_path, caplog):
    maps = _maps(shipped, tmp_path)
    maps.planner(ZONE)
    (tmp_path / "saved" / "Test-T_Isles.npz").write_bytes(b"not a zip")

    with caplog.at_level(logging.WARNING):
        planner = _maps(shipped, tmp_path).planner(ZONE)

    _assert_plans_alike(planner, build_planner(_isles()))
    assert "unreadable" in caplog.text
    assert _maps(shipped, tmp_path).saved(ZONE)


def _member_data_at(data: bytes, name: str) -> int:
    """Where *name*'s compressed bytes start in the zip *data*: past its local header."""
    info = zipfile.ZipFile(io.BytesIO(data)).getinfo(name)
    name_size, extra_size = struct.unpack_from("<HH", data, info.header_offset + 26)
    return info.header_offset + 30 + name_size + extra_size


def _corrupt_deflate_stream(data: bytes) -> bytes:
    """The key's deflate stream opening on a block of the reserved type: ``zlib.error``."""
    damaged = bytearray(data)
    damaged[_member_data_at(data, "key.npy")] = 0b111  # the last block, of type 3
    return bytes(damaged)


def _unknown_zip_version(data: bytes) -> bytes:
    """The first entry of the central directory asking for zip 8.4: ``NotImplementedError``."""
    damaged = bytearray(data)
    (directory,) = struct.unpack_from("<I", data, len(data) - 6)  # the end record's offset, no comment
    damaged[directory + 6] = 84
    return bytes(damaged)


def _garbled_npy_header(data: bytes) -> bytes:
    """The key's ``.npy`` header with a quote gone, compressed whole: tokenize's ``TokenError``."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as copy:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "key.npy":
                header_size = int.from_bytes(content[8:10], "little")
                header = b"{'descr': '<U64".ljust(header_size - 1) + b"\n"
                content = content[:10] + header + content[10 + header_size :]
            copy.writestr(info, content)
    return out.getvalue()


@pytest.mark.parametrize(
    ("damage", "raised"),
    [
        (_corrupt_deflate_stream, zlib.error),
        (_unknown_zip_version, NotImplementedError),
        (_garbled_npy_header, tokenize.TokenError),
    ],
    ids=["corrupt deflate stream", "unknown zip version", "garbled npy header"],
)
def test_a_copy_damaged_inside_its_zip_is_rebuilt_and_warm_rebuilds_it(shipped, tmp_path, caplog, damage, raised):
    maps = _maps(shipped, tmp_path)
    maps.planner(ZONE)
    path = tmp_path / "saved" / "Test-T_Isles.npz"
    path.write_bytes(damage(path.read_bytes()))
    with pytest.raises(raised), np.load(path, allow_pickle=False) as arrays:
        arrays["key"]  # the damage is what it is named for

    assert not _maps(shipped, tmp_path).saved(ZONE)
    with caplog.at_level(logging.WARNING):
        planner = _maps(shipped, tmp_path).planner(ZONE)
    _assert_plans_alike(planner, build_planner(_isles()))
    assert "unreadable" in caplog.text
    assert _maps(shipped, tmp_path).saved(ZONE)

    path.write_bytes(damage(path.read_bytes()))
    assert _maps(shipped, tmp_path).warm([ZONE]) == 1
    assert _maps(shipped, tmp_path).saved(ZONE)


def test_a_copy_that_cannot_be_saved_still_gives_the_planner(shipped, tmp_path, caplog):
    blocked = tmp_path / "saved"
    blocked.write_text("a file where the folder would go", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        planner = ZoneMaps(shipped, blocked).planner(ZONE)

    _assert_plans_alike(planner, build_planner(_isles()))
    assert "could not be saved" in caplog.text


def test_warm_builds_every_copy_missing_and_only_those(shipped, tmp_path):
    maps = _maps(shipped, tmp_path)

    assert maps.warm() == 2
    assert maps.saved(ZONE) and maps.saved(TWIN)
    assert maps.warm() == 0
    assert maps.warm(["Test/T_Elsewhere"]) == 0


def test_warm_stops_when_told(shipped, tmp_path):
    stop = threading.Event()
    stop.set()
    maps = _maps(shipped, tmp_path)

    assert maps.warm(stop=stop) == 0
    assert not maps.saved(ZONE)


# --- the maps core ships -----------------------------------------------------------------------


def test_the_shipped_maps_are_whole_every_file_indexed_and_every_index_entry_there():
    root = zone_maps.MAPS_DIR
    index = ZoneMaps(root).index
    named = {entry["floor"] for entry in index.values()} | {entry["zone"] for entry in index.values()}
    on_disk = {
        f"{folder}/{p.name}" for folder in (zone_maps.FLOORS, zone_maps.ZONES) for p in (root / folder).iterdir()
    }

    assert len(index) > 400
    assert named == on_disk
    for zone, entry in index.items():
        assert zone_maps.read_zone((root / entry["zone"]).read_text(encoding="utf-8")).zone == zone


def test_a_shipped_map_loads_into_a_planner(tmp_path):
    planner = ZoneMaps(cache=tmp_path).planner("WizardCity/WC_Hub")
    assert planner.named and planner.exits and planner.patrols == []
    assert planner.passable.any()


# --- what this module reads ---------------------------------------------------------------------

MOVEMENT = Path(__file__).resolve().parents[3] / "bartleby" / "runtime" / "movement"


def test_zone_maps_imports_no_katsuba_and_reads_no_wad():
    tree = ast.parse((MOVEMENT / "zone_maps.py").read_text(encoding="utf-8"))
    imports = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imports |= {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {name for name in imports if name.split(".")[0] in ("katsuba", "wizwalker")}
    assert ".wad" not in (MOVEMENT / "zone_maps.py").read_text(encoding="utf-8").replace("no wad", "")


#: What builds a saved copy's arrays, and the CACHE_VERSION they were last checked at. When this
#: test fails, a change may build a different copy from the same map: raise
#: ``zone_maps.CACHE_VERSION``, so every user's copies are rebuilt, and set both values here.
BUILT_BY = (
    collision,
    zone_grid,
    patrols.segments,
    patrols.distance_to_segments,
    patrols.clearance,
    route.grid_of,
    route.wall_gates,
)
BUILT_BY_SHA = "b0f8589fab798b857a9d8725051116b7d7cd6a6087c93156b622f308b13d46c4"
CHECKED_AT = 2


def test_cache_version_follows_the_planner():
    digest = hashlib.sha256()
    for source in BUILT_BY:
        digest.update(inspect.getsource(source).replace("\r\n", "\n").encode("utf-8"))
    assert (digest.hexdigest(), zone_maps.CACHE_VERSION) == (BUILT_BY_SHA, CHECKED_AT)
