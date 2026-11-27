"""The shipped zone maps: each zone's planner from its wad, built once, so a run needs no wad.

``tools/build_zone_maps.py`` reads every zone a product walks out of the game's files and
writes what :func:`~bartleby.runtime.movement.route.build_planner` needs of them, under
``bartleby/runtime/game/data/zone_maps/``:

- ``floors/<key>.npz``: the floor, as ``collision.bcd``'s walkable triangles and its solids'
  footprints. Zones whose ``collision.bcd`` is the same file share one, keyed by its hash
  (:func:`floor_key`).
- ``zones/<zone with / as ->.json``: what differs between zones that share a floor: the patrols,
  the named arrival points, the links and the exits, already found, and the gates (``gates.py``),
  each placed object's solids where it stands.
- ``index.json``: for each zone, its two files and the hash of the wad entries it was built from
  (``tools/build_zone_maps.py --verify`` checks those against an install).

Both kinds of file are written byte for byte the same from the same zone, so a rebuild after
a game update changes only the zones the update changed, and ``git diff`` shows which. A floor
is an ``.npz`` of plain arrays, loaded with ``allow_pickle=False``.

**The rest is built on the user's machine and saved there**, in
``%LocalAppData%/Bartleby/cache/zone_maps/`` (:func:`cache_dir`), shared by every product: the
grid, its passable floor and the patrols' clearance, built as they are from a wad
(:func:`~bartleby.runtime.movement.route.grid_of`, :func:`~bartleby.runtime.movement.route.assemble`).
Shipped, those came to about 850 MB in git (the grids 147 MB, the clearance the rest), growing
with every game update; built, they take 2 to 4 s on the biggest streets. So each is built once,
by :meth:`ZoneMaps.warm` ahead of need or by the first load that finds none, and after that a
load reads it back in tens of milliseconds. A saved copy is used only if its key, from the
shipped files' bytes and :data:`CACHE_VERSION`, is the current one; anything else, a damaged
file included, is rebuilt.

This module reads files, unlike the planner modules, but never a wad, and imports no katsuba
(``tests/runtime/movement/test_zone_maps.py`` holds it to that). Core's ``docs/WALKING.md``,
*The shipped zone maps*, has the sizes and the rest.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import hashlib
import io
import json
import logging
import os
import threading
import zipfile
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from bartleby.runtime.movement.collision import Footprint
from bartleby.runtime.movement.gates import Gate
from bartleby.runtime.movement.patrols import Patrol, clearance
from bartleby.runtime.movement.route import ZonePlanner, assemble, grid_of, wall_gates
from bartleby.runtime.movement.zone_grid import Grid
from bartleby.runtime.movement.zone_links import XYZ, Link, Volume
from bartleby.shared.vendor import local_vendor_dir

#: Where the shipped maps live: beside core's other game data, so a packaged build carries them.
MAPS_DIR = Path(__file__).resolve().parent.parent / "game" / "data" / "zone_maps"
INDEX_NAME = "index.json"
FLOORS = "floors"
ZONES = "zones"
#: The maps' layout. A change to what a file holds raises it, and a reader refuses any other.
#: 2: a zone's file holds its gates.
FORMAT = 2
# A zip entry's date, fixed, so the same arrays make the same bytes (the earliest a zip can say).
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def floor_key(collision: bytes) -> str:
    """The name a floor is shipped under: the start of its ``collision.bcd``'s SHA-256."""
    return hashlib.sha256(collision).hexdigest()[:16]


def zone_file_name(zone: str) -> str:
    """``WizardCity/WC_Streets/WC_Unicorn`` -> ``WizardCity-WC_Streets-WC_Unicorn.json``, as its wad is named."""
    return zone.replace("/", "-") + ".json"


# --- a floor ---------------------------------------------------------------------------------


def _npz_bytes(arrays: Mapping[str, np.ndarray], level: int = 9) -> bytes:
    """An ``.npz`` the same bytes every time for the same arrays. ``np.savez_compressed`` stamps
    each entry with the time it was written, so the zip is built here instead, each entry dated
    the same and written in name order."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=level) as archive:
        for name in sorted(arrays):
            info = zipfile.ZipInfo(f"{name}.npy", date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with archive.open(info, "w") as entry:
                np.lib.format.write_array(entry, np.ascontiguousarray(arrays[name]), allow_pickle=False)
    return out.getvalue()


def floor_bytes(triangles: np.ndarray, footprints: Sequence[Footprint]) -> bytes:
    """A floor as an ``.npz`` of plain arrays, the same bytes every time for the same floor: the
    walkable triangles, (N, 3, 3), and each solid's footprint, as ``collision.py`` finds them.

    A footprint is a row of ``footprints``, (center x, center y, radius, zlo, zhi, firm), with
    ``hull_sizes`` corners of ``hulls`` for a box and none for a round one."""
    rows = [(*f.center, f.radius, f.zlo, f.zhi, float(f.firm)) for f in footprints]
    hulls = [np.asarray(f.hull, dtype=np.float64).reshape(-1, 2) for f in footprints if f.hull is not None]
    arrays = {
        "triangles": np.ascontiguousarray(triangles, dtype=np.float64).reshape(-1, 3, 3),
        "footprints": np.array(rows, dtype=np.float64).reshape(-1, 6),
        "hull_sizes": np.array([0 if f.hull is None else len(f.hull) for f in footprints], dtype=np.int32),
        "hulls": np.concatenate(hulls) if hulls else np.empty((0, 2)),
    }
    return _npz_bytes(arrays)


def read_floor(data: bytes) -> tuple[np.ndarray, list[Footprint]]:
    """The triangles and footprints :func:`floor_bytes` wrote."""
    with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
        triangles, rows, sizes, hulls = (arrays[k] for k in ("triangles", "footprints", "hull_sizes", "hulls"))
    footprints = []
    at = 0
    for (x, y, radius, zlo, zhi, firm), size in zip(rows.tolist(), sizes.tolist(), strict=True):
        hull = hulls[at : at + size] if size else None
        at += size
        footprints.append(Footprint(hull, (x, y), radius, zlo, zhi, bool(firm)))
    return triangles, footprints


# --- a zone's own file -----------------------------------------------------------------------


def _xyz(point: Sequence[float]) -> XYZ:
    return (float(point[0]), float(point[1]), float(point[2]))


def _footprint_record(f: Footprint) -> dict:
    return {
        "center": [float(f.center[0]), float(f.center[1])],
        "radius": float(f.radius),
        "zlo": float(f.zlo),
        "zhi": float(f.zhi),
        "firm": bool(f.firm),
        "hull": None if f.hull is None else np.asarray(f.hull, dtype=np.float64).tolist(),
    }


def _footprint(record: Mapping) -> Footprint:
    hull = record["hull"]
    return Footprint(
        None if hull is None else np.array(hull, dtype=np.float64).reshape(-1, 2),
        (float(record["center"][0]), float(record["center"][1])),
        float(record["radius"]),
        float(record["zlo"]),
        float(record["zhi"]),
        bool(record["firm"]),
    )


def zone_text(
    zone: str,
    patrols: Sequence[Patrol],
    named: Mapping[str, XYZ],
    links: Sequence[Link],
    exits: Sequence[Volume],
    gates: Sequence[Gate] = (),
) -> str:
    """A zone's own file: everything of its planner but the floor, in the order it was found
    (a link's order decides which of two at one spot a leg takes). Every float is written as
    Python writes it, which reads back exactly."""
    record = {
        "format": FORMAT,
        "zone": zone,
        "named": {name: list(_xyz(at)) for name, at in named.items()},
        "patrols": [
            {
                "spawner": p.spawner,
                "template": p.template,
                "proximity": float(p.proximity),
                "max_spawns": int(p.max_spawns),
                "start_node_type": int(p.start_node_type),
                "points": [list(_xyz(point)) for point in np.asarray(p.points, dtype=np.float64)],
            }
            for p in patrols
        ],
        "links": [
            {
                "name": link.name,
                "kind": link.kind,
                "source": list(_xyz(link.source)),
                "landing": link.landing,
                "target": list(_xyz(link.target)),
            }
            for link in links
        ],
        "exits": [dataclasses.asdict(exit) | {"center": list(_xyz(exit.center))} for exit in exits],
        "gates": [
            {
                "name": gate.name,
                "template": gate.template,
                "at": list(_xyz(gate.at)),
                "wall": gate.wall,
                "footprints": [_footprint_record(f) for f in gate.footprints],
            }
            for gate in gates
        ],
    }
    return json.dumps(record, indent=1, ensure_ascii=False) + "\n"


@dataclass(frozen=True)
class ZoneRecord:
    """A zone's own file, read: what goes on its floor."""

    zone: str
    patrols: list[Patrol]
    named: dict[str, XYZ]
    links: list[Link]
    exits: list[Volume]
    gates: list[Gate] = dataclasses.field(default_factory=list)


def read_zone(text: str) -> ZoneRecord:
    """The zone :func:`zone_text` wrote. Refuses a file of another format."""
    record = json.loads(text)
    if record.get("format") != FORMAT:
        raise ValueError(f"zone map format {record.get('format')!r}, expected {FORMAT}")
    return ZoneRecord(
        record["zone"],
        [
            Patrol(
                p["spawner"],
                p["template"],
                float(p["proximity"]),
                int(p["max_spawns"]),
                np.array(p["points"], dtype=np.float64).reshape(-1, 3),
                int(p["start_node_type"]),
            )
            for p in record["patrols"]
        ],
        {name: _xyz(at) for name, at in record["named"].items()},
        [Link(k["name"], _xyz(k["source"]), k["landing"], _xyz(k["target"]), k["kind"]) for k in record["links"]],
        [Volume(**(e | {"center": _xyz(e["center"])})) for e in record["exits"]],
        [
            Gate(
                g["name"],
                g["template"],
                _xyz(g["at"]),
                tuple(_footprint(f) for f in g["footprints"]),
                bool(g["wall"]),
            )
            for g in record["gates"]
        ],
    )


def index_text(zones: Mapping[str, Mapping[str, str]]) -> str:
    """``index.json``: each zone's floor, its own file and its wad's hash, zones in name order."""
    return json.dumps({"format": FORMAT, "zones": dict(sorted(zones.items()))}, indent=1, sort_keys=True) + "\n"


# --- the shipped maps, and their saved copies ------------------------------------------------

#: What a saved copy was built by. Raise it whenever a change to the planner modules would build
#: a different grid, floor, clearance or room from walls from the same map: every saved copy is
#: then rebuilt. ``test_cache_version_follows_the_planner`` fails on such a change until it is
#: raised. 3: the room from walls (``Grid.wall_room``), for Stage 1.2 of BartleBot's #340.
CACHE_VERSION = 3
#: How many planners a session keeps in memory, most recently used last: a big street holds
#: some 20 MB of arrays, and reading one back from its saved copy is quick.
KEPT = 8

logger = logging.getLogger(__name__)


def cache_dir() -> Path:
    """``%LocalAppData%/Bartleby/cache/zone_maps``: the saved copies, this machine's, shared by
    every product, and rebuilt from the shipped maps whenever they are gone."""
    return local_vendor_dir() / "cache" / "zone_maps"


@dataclass(frozen=True)
class _Built:
    """What a zone's saved copy holds: the arrays built at load, and the key they were built for."""

    key: str
    grid: Grid
    passable: np.ndarray
    clearance: np.ndarray
    wall_room: np.ndarray | None  # None in a copy saved before CACHE_VERSION 3, whose key no longer fits

    def to_bytes(self) -> bytes:
        return _npz_bytes(
            {
                "key": np.array([self.key]),
                "levels": self.grid.levels,
                "walls": self.grid.walls,
                "origin": np.array([self.grid.x0, self.grid.y0, self.grid.cell], dtype=np.float64),
                "passable": self.passable,
                "clearance": self.clearance,
                "wall_room": self.wall_room,
            },
            level=1,
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> _Built:
        with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
            x0, y0, cell = (float(v) for v in arrays["origin"])
            grid = Grid.from_arrays(arrays["levels"], arrays["walls"], x0, y0, cell)
            wall_room = arrays["wall_room"] if "wall_room" in arrays.files else None
            return cls(str(arrays["key"][0]), grid, arrays["passable"], arrays["clearance"], wall_room)


class ZoneMaps:
    """A folder of zone maps, and the saved copies built from them. The index is read once;
    each planner is read from its saved copy, or built and saved when there is none."""

    def __init__(self, root: Path = MAPS_DIR, cache: Path | None = None) -> None:
        self.root = root
        self._cache = cache
        self._lock = threading.Lock()
        self._zone_locks: dict[str, threading.Lock] = {}
        self._planners: OrderedDict[str, ZonePlanner] = OrderedDict()

    @property
    def cache(self) -> Path:
        """Where the saved copies go: :func:`cache_dir` unless one was given."""
        return self._cache if self._cache is not None else cache_dir()

    @functools.cached_property
    def index(self) -> dict[str, dict[str, str]]:
        """Each zone's entry in ``index.json``; empty when there is none."""
        try:
            record = json.loads((self.root / INDEX_NAME).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if record.get("format") != FORMAT:
            raise ValueError(f"{self.root / INDEX_NAME}: format {record.get('format')!r}, expected {FORMAT}")
        return record["zones"]

    def has(self, zone: str) -> bool:
        """Whether *zone* has a map, from the index alone: no map is opened."""
        return zone in self.index

    def zones(self) -> list[str]:
        return sorted(self.index)

    def planner(self, zone: str) -> ZonePlanner:
        """*zone*'s planner. Each caller gets its own copy of what a walk changes (the floor it
        blocks, the links it drops, the gates it closes); the rest is shared. Blocks: call :meth:`load` from the
        event loop."""
        with self._zone_lock(zone):
            with self._lock:
                kept = self._planners.get(zone)
                if kept is not None:
                    self._planners.move_to_end(zone)
            if kept is None:
                kept = self._read(zone)
                with self._lock:
                    self._planners[zone] = kept
                    while len(self._planners) > KEPT:
                        self._planners.popitem(last=False)
        return dataclasses.replace(
            kept, passable=kept.passable.copy(), links=list(kept.links), closed=[], blocks=[], _took={}
        )

    async def load(self, zone: str) -> ZonePlanner:
        """:meth:`planner`, off the event loop."""
        return await asyncio.to_thread(self.planner, zone)

    def saved(self, zone: str) -> bool:
        """Whether *zone* has a saved copy built from its current map. A damaged one is not."""
        try:
            with np.load(self._cache_path(zone), allow_pickle=False) as arrays:
                saved_key = str(arrays["key"][0])  # only the key is read, not the arrays
        except Exception:  # anything a damaged copy raises: see _saved
            return False
        try:
            return saved_key == self._key(zone)
        except (KeyError, OSError):  # no map of the zone, or its files are gone
            return False

    def warm(self, zones: Sequence[str] | None = None, stop: threading.Event | None = None) -> int:
        """Build and save the copy of every zone in *zones* (every mapped zone by default) that
        has none, one at a time, until *stop* is set; how many it built. Blocks, for minutes
        the first time: run it on a thread of its own."""
        built = 0
        for zone in self.zones() if zones is None else zones:
            if stop is not None and stop.is_set():
                break
            if zone in self.index and not self.saved(zone):
                with self._zone_lock(zone):
                    self._built(zone, self._key(zone))
                built += 1
        return built

    def _zone_lock(self, zone: str) -> threading.Lock:
        with self._lock:
            return self._zone_locks.setdefault(zone, threading.Lock())

    def _files(self, zone: str) -> tuple[bytes, bytes]:
        if zone not in self.index:
            raise KeyError(f"no zone map for {zone!r}")
        entry = self.index[zone]
        return (self.root / entry["floor"]).read_bytes(), (self.root / entry["zone"]).read_bytes()

    def _key(self, zone: str) -> str:
        """What a saved copy of *zone* must have been built from: its map's two files, as they
        are now, and this planner's :data:`CACHE_VERSION`."""
        floor, own = self._files(zone)
        digest = hashlib.sha256(f"{CACHE_VERSION}\0{len(floor)}\0".encode())
        digest.update(floor)
        digest.update(own)
        return digest.hexdigest()

    def _cache_path(self, zone: str) -> Path:
        return self.cache / (zone.replace("/", "-") + ".npz")

    def _saved(self, zone: str, key: str) -> _Built | None:
        """The saved copy, if there is one built for *key*; a damaged one is logged and skipped."""
        try:
            data = self._cache_path(zone).read_bytes()
        except FileNotFoundError:
            return None
        # What a damaged copy raises is whatever zipfile, zlib and numpy's .npy reader happen to
        # raise, which no one of them promises: a corrupt deflate stream raises zlib.error, a
        # flipped version byte NotImplementedError, a garbled .npy header tokenize's TokenError,
        # and others besides. Missing one fails every walk in the zone until the file is deleted
        # by hand; catching too much costs a rebuild of a few seconds, and the warning says why.
        try:
            built = _Built.from_bytes(data)
        except Exception as error:
            logger.warning("zone map %s: its saved copy is unreadable, so it is rebuilt: %s", zone, error)
            return None
        return built if built.key == key else None

    def _built(self, zone: str, key: str) -> _Built:
        """Build *zone*'s arrays from its shipped map, and save them; a save that fails is
        logged, and the arrays are still returned."""
        floor, own = self._files(zone)
        grid = grid_of(*read_floor(floor))
        record = read_zone(own.decode("utf-8"))
        wall_gates(grid, record.gates)
        built = _Built(key, grid, grid.passable(), clearance(grid, record.patrols), grid.wall_room())
        path = self._cache_path(zone)
        partial = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.partial")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial.write_bytes(built.to_bytes())
            os.replace(partial, path)
        except OSError as error:
            logger.warning("zone map %s: its copy could not be saved: %s", zone, error)
            partial.unlink(missing_ok=True)
        return built

    def _read(self, zone: str) -> ZonePlanner:
        key = self._key(zone)
        built = self._saved(zone, key) or self._built(zone, key)
        record = read_zone(self._files(zone)[1].decode("utf-8"))
        return assemble(
            zone,
            built.grid,
            record.patrols,
            record.named,
            record.links,
            record.exits,
            gates=record.gates,
            passable=built.passable,
            room=built.clearance,
            wall_room=built.wall_room,
        )


#: The maps core ships.
SHIPPED = ZoneMaps()


def has(zone: str) -> bool:
    """Whether core ships a map of *zone*: answered from the index, which loads once a session."""
    return SHIPPED.has(zone)


async def load(zone: str) -> ZonePlanner:
    """The planner of a zone core ships a map of, off the event loop: from its saved copy, or
    built and saved the first time."""
    return await SHIPPED.load(zone)
