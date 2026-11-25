"""Reading a zone's wad into the planner's ``ZoneFiles``, with katsuba. Not a script.

Each zone ships as ``GameData/<zone with / as ->.wad``. What the planner is built from (core's
``docs/WALKING.md``, *Planning a route from a zone's files*):

    collision.bcd      the floor, and the solids that wall it
    spawnData.xml      every spawner, its template, and the path it walks
    pathData.xml       each path's node ids, in order
    pathNodeData.bin   each node's position
    gamedata.bin       the named arrival points, and every placed object
                       (some newer zones ship it as gamedata.xml)
    volumes.xml        every volume's name, center, shape and size
    triggers.xml       which volumes teleport
    zone.nav           the quest arrow's graph

and, from ``Root.wad``, the templates: which spawned ones are duelists and their trigger
radius, and which placed ones can be interacted with. ``gamedata.bin`` has no ``BINd`` prefix
and only reads with flags 0; the ``BINd`` files need the stateful flags. Both need the type list
of the game's revision (a ``r<rev>_WizardDev.json`` dump; core's ``docs/GAME_DATA.md``).

Moved from BartleBot's ``tools/probe_walk_route.py``, ``tools/zone_graph/wad_common.py`` and
``tools/zone_graph/zone_data.py`` for ``tools/build_zone_maps.py``; BartleBot's probes import it
from the core checkout beside theirs. Nothing in ``bartleby/`` imports it, since nothing there
reads a wad.
"""

from __future__ import annotations

import functools
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from katsuba.op import STATEFUL_FLAGS, Serializer, SerializerOptions, TypeList
from katsuba.wad import Archive

try:  # katsuba 0.4, BartleBot's, has lazy objects and lists of its own; 0.6, core's, a dict subclass
    from katsuba.op import LazyList, LazyObject

    _OBJECTS: tuple[type, ...] = (LazyObject, dict)
    _LISTS: tuple[type, ...] = (LazyList, list)
except ImportError:
    _OBJECTS, _LISTS = (dict,), (list,)

from bartleby.runtime.movement.collision import CYLINDER, parse_collision
from bartleby.runtime.movement.patrols import Patrol, mob_patrols
from bartleby.runtime.movement.route import ZoneFiles
from bartleby.runtime.movement.zone_links import (
    InteractTemplate,
    PlacedObject,
    parse_interact_template,
    parse_volumes,
    parse_zone_nav,
    teleport_volumes,
)

GAME_DATA = Path(r"C:\ProgramData\KingsIsle Entertainment\Wizard101\Data\GameData")

#: The entries of a zone's wad a map is built from, the record first: its hash is the map's.
MAP_ENTRIES = (
    "gamedata.bin",
    "gamedata.xml",
    "collision.bcd",
    "spawnData.xml",
    "pathData.xml",
    "pathNodeData.bin",
    "volumes.xml",
    "triggers.xml",
    "zone.nav",
)


def root_wad(game_data: Path = GAME_DATA) -> Archive:
    return Archive.mmap(str(game_data / "Root.wad"))


def zone_wad_path(zone: str, game_data: Path = GAME_DATA) -> Path:
    """``WizardCity/WC_Streets/WC_Unicorn`` -> ``GameData/WizardCity-WC_Streets-WC_Unicorn.wad``."""
    return game_data / (zone.replace("/", "-") + ".wad")


def zone_wad(zone: str, game_data: Path = GAME_DATA) -> Archive:
    path = zone_wad_path(zone, game_data)
    if not path.exists():
        raise FileNotFoundError(f"no wad for zone {zone!r} at {path}")
    return Archive.mmap(str(path))


def wad_entry(wad: Archive, name: str) -> bytes | None:
    """One file of a wad, or None when it has none."""
    try:
        return bytes(wad[name])
    except KeyError:
        return None


def entries_hash(wad: Archive, names: Iterable[str] = MAP_ENTRIES) -> str:
    """The SHA-256 of the named entries a wad has, each with its name and length: what changes
    when an update changes anything a zone's map is built from, and nothing else in its wad
    (its models and textures)."""
    digest = hashlib.sha256()
    for name in names:
        data = wad_entry(wad, name)
        if data is not None:
            digest.update(f"{name}\0{len(data)}\0".encode())
            digest.update(data)
    return digest.hexdigest()


def _serializer(types: TypeList, flags: int) -> Serializer:
    opts = SerializerOptions()
    opts.flags = flags
    opts.shallow = False
    opts.skip_unknown_types = True
    return Serializer(opts, types)


class Deserializer:
    """Deserialize either kind of serialized object the zone wads hold.

    ``BINd``-prefixed files (``Compass.xml``, ``spawnData.xml``, most of Root.wad) use the
    stateful flags. A zone's ``gamedata.bin`` has no prefix and only reads with flags 0.
    """

    def __init__(self, types_path: Path) -> None:
        types = TypeList.open(str(types_path))
        self._bind = _serializer(types, STATEFUL_FLAGS)
        self._raw = _serializer(types, 0)

    def __call__(self, data: bytes):
        if data.startswith(b"BINd"):
            return self._bind.deserialize(data[4:])
        return self._raw.deserialize(data)


def to_plain(value):
    """Convert katsuba's objects into JSON-serializable Python values, a vector into [x, y(, z)]
    whichever katsuba made it."""
    if isinstance(value, _OBJECTS):
        if _is_vector(value):
            return [float(value[k]) for k in ("x", "y", "z") if k in value]
        return {k: to_plain(v) for k, v in value.items()}
    if isinstance(value, _LISTS):
        return [to_plain(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if hasattr(value, "x") and hasattr(value, "y"):
        return [value.x, value.y] + ([value.z] if hasattr(value, "z") else [])
    try:
        json.dumps(value)
    except TypeError:
        return repr(value)
    return value


def text(value) -> str:
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")


def _is_vector(value) -> bool:
    """katsuba 0.6 gives a vector as an object of x, y and z; 0.4 as a value with those attributes."""
    return {key for key, _ in value.items()} in ({"x", "y"}, {"x", "y", "z"})


def xyz(value) -> tuple[float, float, float]:
    if hasattr(value, "x"):
        return (value.x, value.y, value.z)
    return (float(value["x"]), float(value["y"]), float(value["z"]))


def parse_lang(raw: bytes) -> dict[str, str]:
    """A ``.lang`` file as {key: display text}: UTF-16LE, a header line, then records of key,
    translator description and text (core's ``tools/dump_realm_names.py`` has the trap)."""
    lines = raw.decode("utf-16-le", errors="replace").lstrip("\ufeff").split("\r\n")
    return {lines[i]: lines[i + 2] for i in range(1, len(lines) - 2, 3) if lines[i]}


def zone_record(zone: str, deserialize: Deserializer, game_data: Path = GAME_DATA):
    """The zone's record. Most zones ship it as gamedata.bin; some newer ones (Selenopolis,
    Nibbleheim Mines) as gamedata.xml. Raises KeyError when the wad has neither."""
    wad = zone_wad(zone, game_data)
    for entry in ("gamedata.bin", "gamedata.xml"):
        data = wad_entry(wad, entry)
        if data is not None:
            return deserialize(data)
    raise KeyError(f"{zone} has no gamedata.bin or gamedata.xml")


def locations(record) -> list[tuple[str, tuple[float, float, float]]]:
    """The record's named arrival points, in its order."""
    return [(text(loc["m_locName"]), xyz(loc["m_location"])) for loc in (record["m_locationList"] or []) if loc]


def template_location(filename: str) -> tuple[str | None, str]:
    """The wad a template's file is in (None for Root.wad) and its name there:
    ``|Darkmoor|WorldData|ObjectData/X.xml`` is ``ObjectData/X.xml`` in ``Darkmoor-WorldData.wad``."""
    if not filename.startswith("|"):
        return None, filename
    *folders, entry = filename[1:].split("|")
    return "-".join(folders) + ".wad", entry


class ZoneReader:
    """Reads zones out of one install's wads, against one type list. Root.wad's templates are
    read once, the first time a zone needs them."""

    def __init__(self, types_path: Path, game_data: Path = GAME_DATA) -> None:
        self.game_data = game_data
        self.deserialize = Deserializer(types_path)
        self._duelists: dict[int, tuple[str, float] | None] = {}
        self._wads: dict[str, Archive | None] = {}

    @functools.cached_property
    def root(self) -> Archive:
        return root_wad(self.game_data)

    def template_bytes(self, filename: str) -> bytes | None:
        """A template's file, or None when no wad has it. Most are in Root.wad; a name of the
        form ``|Darkmoor|WorldData|ObjectData/X.xml`` is ``ObjectData/X.xml`` in that world's
        ``Darkmoor-WorldData.wad`` (Darkmoor's, Karamelle's and Ahmarra's chests, among others)."""
        wad_name, entry = template_location(filename)
        if wad_name is None:
            return wad_entry(self.root, entry)
        if wad_name not in self._wads:
            path = self.game_data / wad_name
            self._wads[wad_name] = Archive.mmap(str(path)) if path.exists() else None
        wad = self._wads[wad_name]
        return None if wad is None else wad_entry(wad, entry)

    def _listed(self, wad: Archive, name: str, key: str) -> list:
        """A list field of one of the zone's files, [] when the file, the field or its value is
        missing: about a third of the zones a bot names have spawn files with nothing in them."""
        try:
            record = to_plain(self.deserialize(bytes(wad[name])))
        except Exception:
            return []
        return [item for item in ((record or {}).get(key) or []) if item]

    @functools.cached_property
    def manifest(self) -> dict[int, str]:
        """Root.wad's TemplateManifest: each template id's file."""
        manifest = to_plain(self.deserialize(bytes(self.root["TemplateManifest.xml"])))["m_serializedTemplates"]
        return {e["m_id"]: e["m_filename"] for e in manifest}

    def duelist(self, template_id: int) -> tuple[str, float] | None:
        """A template's name and trigger radius, if it is a mob (it has DuelistBehavior)."""
        if template_id not in self._duelists:
            self._duelists[template_id] = None
            filename = self.manifest.get(template_id)
            data = self.template_bytes(filename) if filename is not None else None
            if data is not None:
                template = to_plain(self.deserialize(data))
                for b in template.get("m_behaviors") or []:
                    if isinstance(b, dict) and b.get("m_behaviorName") == "DuelistBehavior":
                        self._duelists[template_id] = (Path(filename).stem, float(b.get("m_npcProximity") or 0.0))
        return self._duelists[template_id]

    def mob_patrols(self, zone: str, starts: tuple[int, ...] = (0, 1)) -> list[Patrol]:
        """The zone's patrols, read from its spawn files and Root.wad's templates."""
        wad = zone_wad(zone, self.game_data)
        spawns = self._listed(wad, "spawnData.xml", "m_spawners")
        if not spawns:
            return []
        paths = {p["m_id"]: p.get("m_nodeIDs") or [] for p in self._listed(wad, "pathData.xml", "m_pathList")}
        nodes = {n["m_id"]: n["m_location"] for n in self._listed(wad, "pathNodeData.bin", "m_nodeList")}
        return mob_patrols(spawns, paths, nodes, self.duelist, starts)

    @functools.cached_property
    def templates(self) -> dict[int, InteractTemplate]:
        """Every placed-object template that can be interacted with, by template id."""
        root = self.root
        langs: dict[str, dict[str, str]] = {}

        def lang(stem: str, key: str) -> str:
            if stem not in langs:
                data = wad_entry(root, f"Locale/en-US/{stem}.lang")
                langs[stem] = parse_lang(data) if data is not None else {}
            return langs[stem].get(key, "")

        manifest = self.deserialize(bytes(root["TemplateManifest.xml"]))
        out = {}
        for entry in manifest["m_serializedTemplates"]:
            data = self.template_bytes(text(entry["m_filename"]))
            if data is None:
                continue
            template = parse_interact_template(Path(text(entry["m_filename"])).stem, data, lang)
            if template is not None:
                out[int(entry["m_id"])] = template
        return out

    def placed_objects(self, record) -> list[PlacedObject]:
        """The zone's placed objects whose templates can be interacted with."""
        out = []
        for obj in record["m_objectList"] or []:
            template = self.templates.get(int(obj["m_templateID.m_full"])) if obj else None
            if template is not None:
                out.append(PlacedObject(text(obj["m_zoneTag"]) or template.name, xyz(obj["m_location"]), template))
        return out

    def zone_files(self, zone: str, *, ignore_cylinders: bool = False) -> ZoneFiles:
        """Everything core's planner is built from, read out of the zone's wad. *ignore_cylinders*
        leaves every cylinder off the grid, as reading them wrong once did, so a route runs through
        the trees: a way to make a walk stall, for testing its recovery."""
        wad = zone_wad(zone, self.game_data)
        solids = parse_collision(bytes(wad["collision.bcd"]))
        if ignore_cylinders:
            solids = [s for s in solids if s.proxy != CYLINDER]
        record = zone_record(zone, self.deserialize, self.game_data)
        volumes_data, triggers_data, nav_data = (wad_entry(wad, n) for n in ("volumes.xml", "triggers.xml", "zone.nav"))
        return ZoneFiles(
            zone,
            solids,
            self.mob_patrols(zone),
            dict(locations(record)),
            parse_volumes(volumes_data) if volumes_data else [],
            teleport_volumes(triggers_data) if triggers_data else frozenset(),
            self.placed_objects(record),
            parse_zone_nav(nav_data) if nav_data else None,
        )
