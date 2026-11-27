"""tools/build_zone_maps.py and tools/zone_files.py: which zones, rebuilding byte for byte, and --verify."""

import dataclasses
import io
import json
import zipfile

import pytest

from bartleby.runtime.movement import zone_maps
from tests.runtime.movement.test_zone_maps import TWIN, ZONE, _isles, _ship
from tools import build_zone_maps as build
from tools import zone_files

# --- which zones -----------------------------------------------------------------------------


def test_the_zones_are_the_zone_maps_and_every_zone_param_a_bot_or_subgraph_names(tmp_path):
    nav = tmp_path / "zone_nav.json"
    nav.write_text(
        json.dumps({"worlds": {"WizardCity": {"zones": {"WizardCity/WC_Hub": {}, "WizardCity/WC_Ravenwood": {}}}}}),
        encoding="utf-8",
    )
    bots, subgraphs = tmp_path / "bots", tmp_path / "subgraphs"
    (bots / "01_wizard_city").mkdir(parents=True)
    subgraphs.mkdir()
    bot = {
        "states": [
            {"params": {"zone": "WizardCity/WC_Hub", "zone_list_entry": "Not/AZone"}},
            {"params": {"to_zone": "Krokotopia/KT_Hub", "dungeon_zone": "Krokotopia/KT_Pyramid", "x": 1}},
            {"params": {"zone": ""}, "inputs": {"mark_zone": "MooShu/MS_Hub"}},
        ]
    }
    (bots / "01_wizard_city" / "a.json").write_text(json.dumps(bot), encoding="utf-8")
    (subgraphs / "s.json").write_text(
        json.dumps({"nodes": [{"params": {"from_zone": "Celestia/CL_Hub"}}]}), encoding="utf-8"
    )

    zones = build.zone_set(nav, [bots, subgraphs])

    assert zones == {
        "Celestia/CL_Hub": ["subgraphs"],
        "Krokotopia/KT_Hub": ["bots"],
        "Krokotopia/KT_Pyramid": ["bots"],
        "MooShu/MS_Hub": ["bots"],
        "WizardCity/WC_Hub": ["bots", "map"],
        "WizardCity/WC_Ravenwood": ["map"],
    }


def test_a_names_file_adds_every_zone_it_names_under_its_own_name(tmp_path):
    # BartleBot's Scales of Knowledge table: a station no bot names still gets its map.
    table = tmp_path / "scales_of_knowledge.json"
    station = {"entrance_zone": "Avalon/AV_Hub", "steps": [{"to_zone": "Avalon/AV_Keep"}], "zone": "Avalon/AV_Keep"}
    table.write_text(json.dumps({"scales": [station]}), encoding="utf-8")

    assert build.zone_set(None, [table]) == {
        "Avalon/AV_Hub": ["scales_of_knowledge"],
        "Avalon/AV_Keep": ["scales_of_knowledge"],
    }


@pytest.mark.parametrize("suffix", ["_Challenge", "_Hard", "_Adv", "Adv"])
def test_a_listed_zones_challenge_mode_copy_is_listed_when_the_install_has_its_wad(tmp_path, suffix):
    for zone in ("Aquila/AQ_Keep", f"Aquila/AQ_Keep{suffix}", "Aquila/AQ_Hall"):
        zone_files.zone_wad_path(zone, tmp_path).write_bytes(b"")

    zones = build.with_challenge_copies({"Aquila/AQ_Keep": ["bots"], "Aquila/AQ_Hall": ["map"]}, tmp_path)

    assert zones == {
        "Aquila/AQ_Hall": ["map"],
        "Aquila/AQ_Keep": ["bots"],
        f"Aquila/AQ_Keep{suffix}": ["bots", "challenge"],
    }


def test_a_challenge_copy_a_bot_names_itself_keeps_its_own_reasons(tmp_path):
    zone_files.zone_wad_path("Aquila/AQ_Keep_Hard", tmp_path).write_bytes(b"")
    listed = {"Aquila/AQ_Keep": ["bots"], "Aquila/AQ_Keep_Hard": ["scales_of_knowledge"]}

    assert build.with_challenge_copies(listed, tmp_path) == listed


# --- building ---------------------------------------------------------------------------------


def test_rebuilding_an_unchanged_zone_writes_byte_identical_files():
    first_floor, first_texts = build.build_group([_isles(), _isles(TWIN)])
    second_floor, second_texts = build.build_group([_isles(), _isles(TWIN)])

    assert first_floor == second_floor
    assert first_texts == second_texts
    with zipfile.ZipFile(io.BytesIO(first_floor)) as archive:
        assert {info.date_time for info in archive.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_a_file_already_holding_the_bytes_is_left_alone(tmp_path):
    path = tmp_path / "zones" / "a.json"
    assert build._write(path, b"same")
    assert not build._write(path, b"same")
    assert build._write(path, b"other")
    assert path.read_bytes() == b"other"


# --- the wad's hash, and --verify ----------------------------------------------------------------


def test_a_maps_hash_changes_with_what_it_is_built_from_and_nothing_else():
    wad = {"collision.bcd": b"floor", "gamedata.bin": b"record", "Textures/stone.dds": b"pixels"}
    base = zone_files.entries_hash(wad)

    assert zone_files.entries_hash(wad | {"Textures/stone.dds": b"new pixels"}) == base
    assert zone_files.entries_hash(wad | {"collision.bcd": b"new floor"}) != base
    assert zone_files.entries_hash(wad | {"triggers.xml": b"a trigger"}) != base


def test_gates_never_change_the_links_or_exits_a_zone_is_built_with():
    plain = _isles()
    without = build.build_group([dataclasses.replace(plain, gates=[])])[1][ZONE]
    with_gates = build.build_group([plain])[1][ZONE]

    one, other = json.loads(without), json.loads(with_gates)
    assert (one["links"], one["exits"]) == (other["links"], other["exits"])
    assert other["gates"] and not one["gates"]


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A built map of both test zones, and an install whose wads say what was built from them."""
    root = tmp_path / "maps"
    _ship(root, _isles(), _isles(TWIN))
    wads = {ZONE: {"collision.bcd": b"floor"}, TWIN: {"collision.bcd": b"floor"}}
    index = json.loads((root / zone_maps.INDEX_NAME).read_text(encoding="utf-8"))["zones"]
    for zone, wad in wads.items():
        index[zone]["wad"] = zone_files.entries_hash(wad)
        index[zone]["objects"] = f"gates of {zone}"
    (root / zone_maps.INDEX_NAME).write_text(zone_maps.index_text(index), encoding="utf-8")
    game_data = tmp_path / "GameData"
    game_data.mkdir()
    for zone in wads:
        zone_files.zone_wad_path(zone, game_data).write_bytes(b"")
    monkeypatch.setattr(build, "zone_wad", lambda zone, _game_data: wads[zone])
    return root, game_data, wads


def test_verify_finds_nothing_when_every_wad_is_as_built(install, capsys):
    root, game_data, _wads = install
    assert build.verify([ZONE, TWIN], root, game_data, _gates_as_built) == 0
    assert "2 maps checked; 0 to look at" in capsys.readouterr().out


def test_verify_reports_a_changed_wad_a_missing_wad_and_a_zone_with_no_map(install, capsys):
    root, game_data, wads = install
    wads[ZONE]["collision.bcd"] = b"a new floor"
    zone_files.zone_wad_path(TWIN, game_data).unlink()

    assert build.verify([ZONE, TWIN, "Test/T_New"], root, game_data, _gates_as_built) == 1

    out = capsys.readouterr().out
    assert f"changed: {ZONE}" in out
    assert f"no wad: {TWIN}" in out
    assert "no map: Test/T_New" in out


def test_verify_reports_a_zone_whose_gates_templates_or_solid_files_changed(install, capsys):
    root, game_data, _wads = install

    assert (
        build.verify([ZONE, TWIN], root, game_data, lambda zone: "changed" if zone == TWIN else _gates_as_built(zone))
        == 1
    )

    lines = capsys.readouterr().out.splitlines()
    assert f"  gates changed: {TWIN}" in lines and f"  gates changed: {ZONE}" not in lines


def _gates_as_built(zone: str) -> str:
    return f"gates of {zone}"


# --- reading the wads --------------------------------------------------------------------------


def test_a_world_prefixed_template_is_read_from_that_worlds_data_wad():
    assert zone_files.template_location("|Darkmoor|WorldData|ObjectData/DM-Chest-Rare-001.xml") == (
        "Darkmoor-WorldData.wad",
        "ObjectData/DM-Chest-Rare-001.xml",
    )
    assert zone_files.template_location("ObjectData/WC/Ghost.xml") == (None, "ObjectData/WC/Ghost.xml")


def test_a_zones_wad_is_its_path_with_slashes_as_dashes(tmp_path):
    assert zone_files.zone_wad_path("WizardCity/WC_Streets/WC_Unicorn", tmp_path) == (
        tmp_path / "WizardCity-WC_Streets-WC_Unicorn.wad"
    )


def test_a_vector_reads_as_a_point_whichever_katsuba_made_it():
    class Vector:
        x, y, z = 1.0, 2.0, 3.0

    assert zone_files.xyz({"x": 1.0, "y": 2.0, "z": 3.0}) == zone_files.xyz(Vector()) == (1.0, 2.0, 3.0)
    assert zone_files.to_plain({"m_location": {"x": 1.0, "y": 2.0, "z": 3.0}, "m_name": b"A"}) == {
        "m_location": [1.0, 2.0, 3.0],
        "m_name": "A",
    }
