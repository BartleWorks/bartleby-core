"""Build the shipped zone maps from the game's wads, check them against an install, or compare them.

Writes ``bartleby/runtime/game/data/zone_maps/`` (``bartleby.runtime.movement.zone_maps`` has
the layout): one floor file per distinct ``collision.bcd``, one small file per zone, and
``index.json``. The same zone always makes the same bytes, so after a game update a rebuild
changes only what the update changed, and ``git status`` lists it.

WHICH ZONES
    Core reads no product's files at run time, so the product says which zones to map: every
    zone in ``--zone-nav`` (BartleBot's ``bartlebot/game/data/zone_nav.json``, each world's
    ``zones``), and every zone a file under each ``--names`` folder names in a ``zone`` or
    ``*_zone`` param, as BartleBot's ``restricted_zones`` reads them (its ``bots/`` and
    ``subgraphs/``). A zone the install has no wad for is reported and left out.

WHAT IT NEEDS
    The game's ``Data/GameData`` folder, with every zone's wad: a live install lacks the wads of
    zones its player never visited, so build on a machine that has them all. And the type list
    of the game's revision, BartleBot's ``bartlebot/game/data/r<rev>_WizardDev.json``.

USAGE
    uv run python tools/build_zone_maps.py --types ../BartleBot/bartlebot/game/data/r814353_WizardDev.json \\
        --zone-nav ../BartleBot/bartlebot/game/data/zone_nav.json \\
        --names ../BartleBot/bots --names ../BartleBot/subgraphs
    ... --zone WizardCity/WC_Streets/WC_Unicorn        # rebuild only these, keep the rest
    ... --verify                                       # no write: every map against the install
    ... --compare WizardCity/WC_Streets/WC_Unicorn     # plan on the map and on the wad, and diff

``--verify`` reports every zone whose wad entries changed since its map was built (the
``index.json`` hash), every mapped zone whose wad the install lacks, and every zone the lists
name that has no map; it exits 1 when any is found. It does not see a change in Root.wad's
templates (a mob's trigger radius, what a placed object does), which a full rebuild does.

``--compare`` builds each zone's planner from its wad and from its shipped map, checks the two
grids, floors, links and exits are the same, and plans a route between every pair of named
arrival points (``--pairs`` at most) on both, shortest and avoiding patrols, cell for cell.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bartleby.runtime.movement import zone_maps  # noqa: E402
from bartleby.runtime.movement.collision import blocking_footprints, walkable_triangles  # noqa: E402
from bartleby.runtime.movement.route import ZoneFiles, build_planner, grid_of, links_and_exits  # noqa: E402
from bartleby.runtime.movement.zone_grid import pieces  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

from zone_files import GAME_DATA, ZoneReader, entries_hash, zone_wad, zone_wad_path  # noqa: E402

# --- which zones -----------------------------------------------------------------------------


def _zone_params(value) -> Iterable[str]:
    """Every string under a ``zone`` or ``*_zone`` key, at any depth: a node's params, a
    subgraph instance's inputs."""
    if isinstance(value, dict):
        for key, inner in value.items():
            if (key == "zone" or key.endswith("_zone")) and isinstance(inner, str) and "/" in inner:
                yield inner
            else:
                yield from _zone_params(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _zone_params(inner)


def zone_set(zone_nav: Path | None, folders: Sequence[Path]) -> dict[str, list[str]]:
    """Every zone the product walks, and why: ``"map"`` for the zone map, a folder's name for a
    file in it."""
    zones: dict[str, set[str]] = {}
    if zone_nav is not None:
        nav = json.loads(zone_nav.read_text(encoding="utf-8"))
        for world in nav["worlds"].values():
            for zone in world.get("zones", {}):
                zones.setdefault(zone, set()).add("map")
    for folder in folders:
        for path in sorted(folder.rglob("*.json")):
            for zone in _zone_params(json.loads(path.read_text(encoding="utf-8"))):
                zones.setdefault(zone, set()).add(folder.name)
    return {zone: sorted(why) for zone, why in sorted(zones.items())}


# --- building ----------------------------------------------------------------------------------


def build_group(group: Sequence[ZoneFiles]) -> tuple[bytes, dict[str, str]]:
    """The floor file and each zone's own file for zones sharing one ``collision.bcd``: the
    grid built once, each zone's links and exits found on it as ``build_planner`` finds them."""
    triangles, footprints = walkable_triangles(group[0].solids), blocking_footprints(group[0].solids)
    grid = grid_of(triangles, footprints)
    passable = grid.passable()
    label = pieces(grid, passable)
    texts = {}
    for files in group:
        links, exits = links_and_exits(files, grid, passable, label)
        texts[files.zone] = zone_maps.zone_text(files.zone, files.patrols, files.named, links, exits)
    return zone_maps.floor_bytes(triangles, footprints), texts


_reader: ZoneReader | None = None


def _start_worker(types: Path, game_data: Path) -> None:
    global _reader
    _reader = ZoneReader(types, game_data)


def _build_task(key: str, zones: list[str]) -> tuple[str, bytes, dict[str, str], dict[str, str], float]:
    started = time.perf_counter()
    group = [_reader.zone_files(zone) for zone in zones]
    floor, texts = build_group(group)
    hashes = {zone: entries_hash(zone_wad(zone, _reader.game_data)) for zone in zones}
    return key, floor, texts, hashes, time.perf_counter() - started


def _write(path: Path, data: bytes) -> bool:
    """Write *data* unless the file already holds it; whether it changed."""
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def groups_of(zones: Iterable[str], game_data: Path) -> tuple[dict[str, list[str]], list[str]]:
    """The zones by their floor's key, and the zones the install has no usable wad for."""
    groups: dict[str, list[str]] = {}
    missing = []
    for zone in zones:
        if not zone_wad_path(zone, game_data).exists():
            missing.append(zone)
            continue
        try:
            collision = bytes(zone_wad(zone, game_data)["collision.bcd"])
        except KeyError:
            missing.append(zone)
            continue
        groups.setdefault(zone_maps.floor_key(collision), []).append(zone)
    return groups, missing


def build(zones: Sequence[str], out: Path, types: Path, game_data: Path, jobs: int, *, full: bool) -> int:
    """Build *zones*' maps into *out*. A *full* build leaves only these zones in the index and
    deletes every file no zone uses; otherwise the other zones' maps are kept."""
    started = time.perf_counter()
    groups, missing = groups_of(zones, game_data)
    index_path = out / zone_maps.INDEX_NAME
    index = {}
    if not full and index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))["zones"]
    failed: dict[str, str] = {}
    changed = 0
    with ProcessPoolExecutor(jobs, initializer=_start_worker, initargs=(types, game_data)) as pool:
        tasks = {pool.submit(_build_task, key, members): (key, members) for key, members in groups.items()}
        for done, task in enumerate(as_completed(tasks), 1):
            key, members = tasks[task]
            try:
                key, floor, texts, hashes, seconds = task.result()
            except Exception as error:  # one bad zone is reported, not the end of the build
                for zone in members:
                    failed[zone] = f"{type(error).__name__}: {error}"
                continue
            floor_name = f"{zone_maps.FLOORS}/{key}.npz"
            changed += _write(out / floor_name, floor)
            for zone, text in texts.items():
                zone_name = f"{zone_maps.ZONES}/{zone_maps.zone_file_name(zone)}"
                changed += _write(out / zone_name, text.encode("utf-8"))
                index[zone] = {"floor": floor_name, "zone": zone_name, "wad": hashes[zone]}
            print(f"[{done}/{len(tasks)}] {', '.join(members)}  [{seconds:.1f}s]", flush=True)
    for zone in failed:
        index.pop(zone, None)
    changed += _write(index_path, zone_maps.index_text(index).encode("utf-8"))
    if full:
        used = {entry["floor"] for entry in index.values()} | {entry["zone"] for entry in index.values()}
        for folder in (zone_maps.FLOORS, zone_maps.ZONES):
            for path in sorted((out / folder).glob("*")):
                if f"{folder}/{path.name}" not in used:
                    path.unlink()
                    changed += 1
                    print(f"removed {folder}/{path.name}: no zone uses it")
    sizes = sorted((p.stat().st_size, p) for p in out.rglob("*") if p.is_file())
    floors = {entry["floor"] for entry in index.values()}
    print(
        f"\n{len(index)} zones on {len(floors)} floors, {sum(s for s, _ in sizes) / 1e6:.1f} MB in all "
        f"(largest {sizes[-1][1].name}, {sizes[-1][0] / 1e6:.1f} MB); {changed} files written or removed  "
        f"[{time.perf_counter() - started:.0f}s]"
    )
    for zone in missing:
        print(f"  no wad: {zone}")
    for zone, why in sorted(failed.items()):
        print(f"  failed: {zone}: {why}")
    return 1 if missing or failed else 0


# --- checking ----------------------------------------------------------------------------------


def verify(zones: Sequence[str], out: Path, game_data: Path) -> int:
    """Report every map whose wad changed or is gone, and every listed zone with no map."""
    index = zone_maps.ZoneMaps(out).index
    problems = 0
    for zone in sorted(index):
        if not zone_wad_path(zone, game_data).exists():
            print(f"  no wad: {zone}")
            problems += 1
        elif entries_hash(zone_wad(zone, game_data)) != index[zone]["wad"]:
            print(f"  changed: {zone}")
            problems += 1
    for zone in zones:
        if zone not in index:
            print(f"  no map: {zone}")
            problems += 1
    print(f"{len(index)} maps checked; {problems} to look at")
    return 1 if problems else 0


def _differences(wad, shipped) -> list[str]:
    """What of two planners is not the same, array for array."""
    same = {
        "levels": np.array_equal(wad.grid.levels, shipped.grid.levels, equal_nan=True),
        "walls": np.array_equal(wad.grid.walls, shipped.grid.walls),
        "origin": (wad.grid.x0, wad.grid.y0, wad.grid.cell) == (shipped.grid.x0, shipped.grid.y0, shipped.grid.cell),
        "passable": np.array_equal(wad.passable, shipped.passable),
        "clearance": np.array_equal(wad.clearance, shipped.clearance),
        "cost": np.array_equal(wad.cost, shipped.cost),
        "named": wad.named == shipped.named,
        "patrols": [(p.spawner, p.proximity, p.points.tolist()) for p in wad.patrols]
        == [(p.spawner, p.proximity, p.points.tolist()) for p in shipped.patrols],
        "links": wad.links == shipped.links,
        "exits": wad.exits == shipped.exits,
    }
    return [name for name, ok in same.items() if not ok]


def compare(zones: Sequence[str], out: Path, types: Path, game_data: Path, pairs: int) -> int:
    """Plan on each zone's map and on its wad, and report any difference. The map is loaded
    twice, into a scratch folder of saved copies: built, as a first load is, then read back."""
    reader = ZoneReader(types, game_data)
    differ = 0
    with tempfile.TemporaryDirectory() as cache:
        for zone in zones:
            wad = build_planner(reader.zone_files(zone))
            started = time.perf_counter()
            cold = zone_maps.ZoneMaps(out, Path(cache)).planner(zone)
            built = time.perf_counter() - started
            started = time.perf_counter()
            warm = zone_maps.ZoneMaps(out, Path(cache)).planner(zone)
            read = time.perf_counter() - started
            bad = sorted(set(_differences(wad, cold)) | set(_differences(wad, warm)))
            ends = list(itertools.permutations(wad.named.items(), 2))
            step = max(1, math.ceil(len(ends) / pairs)) if pairs else 1
            routes = mismatched = 0
            for (_, a), (_, b) in ends[::step]:
                for avoid in (False, True):
                    one, other = wad.route(a, b, avoid), warm.route(a, b, avoid)
                    routes += 1
                    if (one is None) != (other is None) or (
                        one is not None and (one.nodes != other.nodes or wad.legs(one) != warm.legs(other))
                    ):
                        mismatched += 1
            differ += bool(bad or mismatched)
            print(
                f"{zone}: {'same' if not bad else 'differs in ' + ', '.join(bad)}; "
                f"{routes - mismatched} of {routes} routes the same cell for cell, legs included; "
                f"loaded in {built:.2f}s built, {read:.3f}s saved"
            )
    return 1 if differ else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--types", type=Path, required=True, help="the revision's r<rev>_WizardDev.json type list")
    ap.add_argument("--game-data", type=Path, default=GAME_DATA, help="the game's Data/GameData folder")
    ap.add_argument("--zone-nav", type=Path, help="a zone map whose worlds' zones to build (BartleBot's zone_nav.json)")
    ap.add_argument("--names", type=Path, action="append", default=[], help="a folder of bots or subgraphs; repeatable")
    ap.add_argument("--zone", action="append", default=[], help="only this zone; repeatable")
    ap.add_argument("--out", type=Path, default=zone_maps.MAPS_DIR)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="check the maps against the install; write nothing")
    mode.add_argument("--compare", action="store_true", help="plan on the map and on the wad of each --zone")
    ap.add_argument("--pairs", type=int, default=60, help="with --compare, at most this many pairs of points a zone")
    args = ap.parse_args()

    listed = list(zone_set(args.zone_nav, args.names)) if args.zone_nav or args.names else []
    if args.compare:
        if not args.zone:
            ap.error("--compare needs --zone")
        return compare(args.zone, args.out, args.types, args.game_data, args.pairs)
    if args.verify:
        return verify(args.zone or listed, args.out, args.game_data)
    if not (args.zone or listed):
        ap.error("name the zones: --zone-nav, --names or --zone")
    return build(args.zone or listed, args.out, args.types, args.game_data, args.jobs, full=not args.zone)


if __name__ == "__main__":
    sys.exit(main())
