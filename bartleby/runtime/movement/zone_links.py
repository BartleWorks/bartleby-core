"""What moves a wizard within a zone, and what would take it out of one.

Some zones are pieces of floor no walk joins: Triton Avenue's two halves, its ledge and its
isle. The game joins them with links: a volume the wizard steps into, or a placed object it
presses the interact key at (a fast-travel stone, a zipline, a ladder). Either way the server
moves the wizard to an arrival point, and keeps it there, because the move was its own. No
client file says where a link lands but a carpet ride's, so a landing is otherwise put together
from what the files do keep, in the order core's ``docs/WALKING.md`` (*In-zone links*) gives,
each with the evidence it rests on. None of it is the server's word, so a walk drops a link that moves nobody and
replans. Moved from BartleBot's ``tools/probe_walk_route.py`` and
``tools/zone_graph/zone_nav.py``.

A zone's **exits** are the volumes that teleport a wizard and are not one of its links: a
route keeps out of every one but the exit it is sent to. Which volumes teleport is read from
``triggers.xml`` (:func:`teleport_volumes`), and how big each is from ``volumes.xml``
(:func:`parse_volumes`).

Nothing here opens a file: each parser takes the bytes a tool read out of the zone's wad, and
the placed objects come in with their templates already looked up.
"""

from __future__ import annotations

import math
import re
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from bartleby.runtime.movement.zone_grid import Grid, nearest_node

XYZ = tuple[float, float, float]

# --- volumes.xml ---------------------------------------------------------------------------
#
# katsuba cannot deserialize the file with our type dump, but its layout is plain enough to read
# directly: property records of a u32 size in bits, a u32 property hash, then the value. A
# volume's position is three 96-bit float records with consecutive hashes; its name is the
# nearest length-prefixed string ending before them (the first byte is the length times two).
# After the position come its shape and size: m_radius, and for a box m_length, m_width and a
# third side, the hashes being the ones BGSigilProxyBehavior's properties of those names have
# in the type dump. A volume with no shape record is a sphere.

_FLOAT_RECORD = struct.pack("<I", 96)
_POSITION_HASHES = (bytes.fromhex("28f8b37d"), bytes.fromhex("29f8b37d"), bytes.fromhex("2af8b37d"))
_NAME_LOOKBACK = 200
_SHAPE = "ccb28789"
_RADIUS = "df33f93a"
_LENGTH = "3915482d"
_WIDTH = "97f5eb35"
_HEIGHT = "8c259234"
_ID = "01341840"
#: The records a volume has after its position, in some order: an id, the shape and four sizes.
_SIZE_KEYS = frozenset({_ID, _SHAPE, _RADIUS, _LENGTH, _WIDTH, _HEIGHT})


@dataclass(frozen=True)
class Volume:
    """A volume of ``volumes.xml``: its name, its center, and its shape's size.

    A box's sides are read as full lengths, as ``collision.bcd``'s are, and its turn is not in
    the file, so :attr:`reach` takes the circle that holds it however it is turned."""

    name: str
    center: XYZ
    shape: str = "Sphere"
    radius: float = 0.0
    length: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def reach(self) -> float:
        """How far across the floor from its center the volume can reach."""
        if self.shape == "Box":
            return math.hypot(self.length, self.width) / 2
        return self.radius

    @property
    def rise(self) -> float:
        """How far above or below its center the volume can reach."""
        return self.height if self.shape == "Box" else self.radius


def _name_before(data: bytes, end: int) -> str:
    best: tuple[int, int] | None = None  # (end of string, length)
    for p in range(end - 4, max(end - _NAME_LOOKBACK, 0), -1):
        if data[p] & 1:
            continue
        length = data[p] >> 1
        stop = p + 1 + length
        if length < 3 or stop > end or not all(0x20 <= b <= 0x7E for b in data[p + 1 : stop]):
            continue
        if best is None or (stop, length) > best:
            best = (stop, length)
    return data[best[0] - best[1] : best[0]].decode("ascii") if best else "?"


def _records_after(data: bytes, at: int) -> dict[str, bytes]:
    """The volume's records from *at* on, by hash, until one is not a volume's shape or size."""
    out: dict[str, bytes] = {}
    while at + 8 <= len(data):
        bits, key = struct.unpack_from("<II", data, at)
        name = key.to_bytes(4, "little").hex()
        if bits < 64 or bits > 4000 or bits % 8 or name not in _SIZE_KEYS or name in out:
            break
        out[name] = data[at + 8 : at + bits // 8]
        at += bits // 8
    return out


def parse_volumes(data: bytes) -> list[Volume]:
    """Every volume in a zone's ``volumes.xml``, in file order."""
    x_key = _FLOAT_RECORD + _POSITION_HASHES[0]
    found = []
    at = data.find(x_key)
    while at >= 0:
        y_at, z_at = at + 12, at + 24
        if data[y_at + 4 : y_at + 8] == _POSITION_HASHES[1] and data[z_at + 4 : z_at + 8] == _POSITION_HASHES[2]:
            x, y, z = (struct.unpack_from("<f", data, off + 8)[0] for off in (at, y_at, z_at))
            records = _records_after(data, z_at + 12)

            def number(key: str, records: dict[str, bytes] = records) -> float:
                value = records.get(key, b"")
                return struct.unpack_from("<f", value)[0] if len(value) == 4 else 0.0

            shape = records.get(_SHAPE, b"")
            found.append(
                Volume(
                    _name_before(data, at),
                    (x, y, z),
                    shape[1 : 1 + (shape[0] >> 1)].decode("latin-1") if shape else "Sphere",
                    number(_RADIUS),
                    number(_LENGTH),
                    number(_WIDTH),
                    number(_HEIGHT),
                )
            )
        at = data.find(x_key, at + 1)
    return found


# --- triggers.xml --------------------------------------------------------------------------
#
# katsuba cannot read this file either. Each trigger is a run of records starting with its name
# (property hash 100cc9b8); the events it fires on are strings "Enter_<volume>" or
# "Exit_<volume>" inside that run; and a trigger that teleports has a ResTeleport result, whose
# class hash appears in the run. ResTeleport keeps no properties in the client's copy, so where
# it goes is not known, only that it moves the wizard. Checked against zone_nav.json's 334
# verified gate points in 2026-11: every one lies in a volume such a trigger fires on.

_TRIGGER_NAME = bytes.fromhex("100cc9b8")
_RES_TELEPORT = struct.pack("<I", 0x0DA3207D)
_EVENT = re.compile(rb"(Enter|Exit)_")


def _trigger_starts(data: bytes) -> list[int]:
    """Where each trigger's name record starts."""
    out = []
    at = data.find(_TRIGGER_NAME)
    while at >= 4:
        bits = struct.unpack_from("<I", data, at - 4)[0]
        length = data[at + 4] if at + 4 < len(data) else 1
        if bits % 8 == 0 and length % 2 == 0 and (length >> 1) == bits // 8 - 9:
            out.append(at - 4)
        at = data.find(_TRIGGER_NAME, at + 1)
    return out


def teleport_volumes(data: bytes) -> frozenset[str]:
    """The name of every volume that a teleporting trigger of ``triggers.xml`` fires on when
    a wizard enters it."""
    starts = _trigger_starts(data)
    out: set[str] = set()
    for start, end in zip(starts, [*starts[1:], len(data)]):
        run = data[start:end]
        if _RES_TELEPORT not in run:
            continue
        for m in _EVENT.finditer(run):
            p = m.start() - 1
            if p < 0 or run[p] & 1:
                continue
            event = run[p + 1 : p + 1 + (run[p] >> 1)]
            if event.startswith(b"Enter_") and all(0x20 <= b <= 0x7E for b in event):
                out.add(event[len(b"Enter_") :].decode("ascii"))
    return frozenset(out)


# --- zone.nav ------------------------------------------------------------------------------
#
# The graph the quest arrow routes a wizard through one zone. It is zonemap.nav's layout without
# the name table, and with the 12 bytes that are zero there holding each node's position:
#
#     u16  node_count - 1 (or so: one less than the next field in every zone seen)
#     u16  node_count
#     u16  unknown (0)
#     node_count x 14-byte node records: f32 x, y, z, then a u16 that is mostly the node's index
#              (Triton Avenue repeats one, so nothing trusts it)
#     u32  edge_count
#     edge_count x (u16 a, u16 b) node-index pairs, nearly every one stored both ways
#
# Most nodes sit on a 480-unit grid over the walkable floor, joined to their neighbors (480, or
# 679 diagonally). The rest of the edges are the zone's links as the arrow sees them: Triton
# Avenue's teleport pads, Floating Mountains' ziplines, ropes, boats and mine carts elsewhere,
# each a long edge from near where the link is taken to near where it lands. An edge does not
# say what it is or which way it may be taken, and it can end a few hundred units from either.

_NAV_NODE_SIZE = 14
# Longer than two grid steps, diagonally: not a walk between neighbors.
LONG_EDGE = 1500.0
# Or steeper than any walk and higher than a stair: a ladder or a rope (Tyrian Gorge's rise
# 1,100-2,100 over 200-460 across; the grid's steepest edges rise about 300 over 679).
CLIMB = 500.0


@dataclass
class ZoneNav:
    nodes: list[XYZ]
    pairs: list[tuple[int, int]]

    def link_edges(self, longer_than: float = LONG_EDGE) -> list[tuple[int, int]]:
        """The edges no walk takes: spanning more than *longer_than* across the floor, or
        climbing more than CLIMB and more steeply than 1:1."""
        out = []
        for a, b in self.pairs:
            (ax, ay, az), (bx, by, bz) = self.nodes[a], self.nodes[b]
            across, rise = math.hypot(bx - ax, by - ay), abs(bz - az)
            if across > longer_than or rise > max(CLIMB, across):
                out.append((a, b))
        return out


def parse_zone_nav(data: bytes) -> ZoneNav:
    """A zone's ``zone.nav``; ValueError when its sizes do not add up."""
    _, node_count, _ = struct.unpack_from("<HHH", data, 0)
    off = 6
    nodes = []
    for _ in range(node_count):
        nodes.append(struct.unpack_from("<fff", data, off))
        off += _NAV_NODE_SIZE
    (edge_count,) = struct.unpack_from("<I", data, off)
    off += 4
    if off + 4 * edge_count != len(data):
        raise ValueError(f"{len(data) - off - 4 * edge_count} bytes left over after {edge_count} edges")
    pairs = [struct.unpack_from("<HH", data, off + 4 * i) for i in range(edge_count)]
    bad = [p for p in pairs if max(p) >= node_count]
    if bad:
        raise ValueError(f"{len(bad)} edges reference a node out of range, e.g. {bad[0]}")
    return ZoneNav(nodes, pairs)


# --- links ---------------------------------------------------------------------------------

LINK_REACH = 100.0  # floor this near a volume's center steps into it (Triton's are some 177 across)
LINK_SECONDS = 4.0  # what taking a link costs a route, in seconds of walking: the move, and a margin
INTERACT_REACH = 150.0  # floor this near the object's center is near enough to press it
INTERACT_SECONDS = 6.0  # the press, the teleport, a margin
RIDE_SECONDS = 20.0  # a zipline's ride, before one is timed
NAV_OBJECT_REACH = 700.0  # a zone.nav edge's end, or a ride's first end, this near an object is its
RIDE_LANDING_REACH = 1000.0  # a ride lands on the arrival point this near its far end (800-920 seen)
# A pair's end lands on an arrival point this near its partner (Dark Water Trench's whalebirds
# 600 off, as pressed; Ruined Alcazar's isle teleports up to 1,600). With none so near, the two are
# no pair: the Krokotopia Crypt's twin "Teleporter-Static" have only a point 10,000 away.
PAIR_LANDING_REACH = 2000.0
PAD_REACH = 300.0  # an object this near a volume is the pad drawn at it
SHORTCUT_MIN = 1000.0  # a press that moves the wizard less than this, up included, is no link worth routing through

_LINK_VOLUME = re.compile(r"teleport|activator", re.IGNORECASE)
# zone.nav pairs a volume whose name says it moves the wizard, or one a teleporting trigger fires
# on: an activator is as often a quest's trigger (Triton's "Activator Volume Kraken" spawns the
# boss on Kraken Isle), so its name alone is not enough, but triggers.xml says which teleport.
_MOVING_VOLUME = re.compile(r"teleport|(?<![a-z])to(?![a-z])", re.IGNORECASE)
_FILLER = {"target", "location", "teleport", "teleportvol", "activator", "volume", "the", "trigger"}
_DIRECTIONS = {"to", "from"}
_TRAVEL_PROMPT = re.compile(r"\b(teleport|ride|climb|descend|ascend|travel|exit|swim)\b", re.IGNORECASE)
# An object with no prompt the files resolve is taken by its template's name instead.
_TRAVEL_NAME = re.compile(r"teleport|rope|ladder|zip.?line|boat|elevator|lift|cart|dingh?y|portal", re.IGNORECASE)
# A pad that says what it is.
_PAD_VOLUME = re.compile(r"teleport", re.IGNORECASE)
_STONE = re.compile(r"Teleporter([A-F])(?![a-z])")  # not TeleporterArch
_STONE_BACK = re.compile(r"To(Main|Hub)|BackToHub", re.IGNORECASE)
# Dragonspyre's stones name a letter and a role, in either order: the template
# DS_TeleportStoneA_Main, the tag ArenaTeleportStoneMainA. Main stands at the hub, Secondary at
# the far end. Not the hub's own DS_TeleportStoneMain, which has no letter and goes nowhere.
_TELEPORT_STONE = re.compile(
    r"TeleportStone_?(?:(?P<letter>[A-F])_?(?P<role>Main|Secondary)|(?P<role2>Main|Secondary)_?(?P<letter2>[A-F]))(?![A-Za-z])"
)


def _stone(obj: PlacedObject) -> tuple[str, bool] | None:
    """A fast-travel stone's letter, and whether it is the one that goes back to the hub."""
    if m := _STONE.search(obj.tag):
        return m.group(1), bool(_STONE_BACK.search(obj.tag))
    for name in (obj.template.name, obj.tag):
        if m := _TELEPORT_STONE.search(name):
            return m["letter"] or m["letter2"], (m["role"] or m["role2"]) == "Secondary"
    return None


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if w} - _FILLER


@dataclass(frozen=True)
class Link:
    """Where a link is taken, and the arrival point it lands on."""

    name: str  # the volume's, or the placed object's zone tag
    source: XYZ  # the volume's center, or the object's
    landing: str
    target: XYZ  # where it lands: an arrival point, a ride's far end, a nav node
    # "volume" is stepped into; "interact" and "ride" are pressed, "ride" carrying the wizard over.
    kind: str = "volume"

    @property
    def reach(self) -> float:
        """How near the source a wizard stands to take the link."""
        return LINK_REACH if self.kind == "volume" else INTERACT_REACH

    @property
    def seconds(self) -> float:
        return {"volume": LINK_SECONDS, "interact": INTERACT_SECONDS}.get(self.kind, RIDE_SECONDS)


def link_candidates(volumes: Sequence[Volume], named: Mapping[str, XYZ]) -> list[Link]:
    """Each teleport or activator volume, with the arrival point its name best matches: "to"
    and "from" break a tie ("From Kraken Isle" lands at "From_Kraken_Isle", not
    "To_Kraken_Isle"), and two landings fitting as well is no link. Cinematic triggers are left
    out."""
    out = []
    for volume in volumes:
        name = volume.name
        words = _words(name)
        if not _LINK_VOLUME.search(name) or "cinematic" in words:
            continue
        core, way = words - _DIRECTIONS, words & _DIRECTIONS
        scored = []
        for landing, target in named.items():
            other = _words(landing)
            shared = len(core & (other - _DIRECTIONS))
            if shared and "cinematic" not in other:
                scored.append((2 * shared + (way == other & _DIRECTIONS), landing, target))
        if not scored:
            continue
        scored.sort(key=lambda row: row[0], reverse=True)
        if len(scored) > 1 and scored[1][0] == scored[0][0]:
            continue
        out.append(Link(name, tuple(volume.center), scored[0][1], tuple(scored[0][2])))
    return out


# --- interact links ------------------------------------------------------------------------
#
# Most of the game's in-zone links are not volumes but placed objects the wizard presses the
# interact key at. The behavior that moves the wizard is stripped from the client's copy of every
# one (DeletedBehavior), so where each lands is put together from what the files do keep:
#
#   - a carpet ride's template names the arrival point it lands on;
#   - another ride's template (a zipline's) holds its two ends as LOCATION records: it lands on the
#     arrival point nearest the second (both ziplines pressed live did, 3 units off);
#   - a stone of a TeleporterX family lands at the arrival point nearest its partner: X at the
#     one nearest X_ToMain, X_ToMain at the one nearest the hub's stones (Triton's four, pressed
#     live, all did);
#   - a two-way pair (X and X_B or X-A, two sharing a tag, a teleporter's _TOP and _BOTTOM):
#     each at the arrival point nearest the other;
#   - zone.nav has an edge no walk takes for most links: one starting near a placed object whose
#     prompt is a travel verb is that object's link, to the arrival point nearest the edge's far
#     end, or the far end itself. zone_links does the same for a volume its name does not pair
#     with another piece.

# A template's InteractableBehavior: its prompt's GUI.lang key, and each LOCATION record's
# name, position and zone, by property hash.
_INTERACTABLE = b"InteractableBehavior"
_PROMPT_KEY = re.compile(rb"([A-Za-z]+)_(\d{8})")
_PROMPT_SPAN = 600
_LOCATION_NAME = bytes.fromhex("cbdec46e")
_LOCATION_XYZ = tuple(bytes.fromhex(h) for h in ("bffe5a5a", "c0fe5a5a", "c1fe5a5a"))
_LOCATION_ZONE = bytes.fromhex("c122a8ba")
# A template's interaction can begin with the quests it belongs to, each by its title, before the
# prompt: Ahmarra's "Hooper Trooper" opens with "Jelly School" and only then says Teleport.
_QUEST_TITLES = "QuestTitle"
# An InteractCarpetRide (Ahmarra's leaves and wing harnesses, the Oasis's boats) names the zone it
# is in and the arrival point it carries the wizard to, each a length-prefixed string by property
# hash. The one place a client file says where a link lands.
_CARPET_RIDE = b"InteractCarpetRide"
_RIDE_ZONE = bytes.fromhex("79944a9b")
_RIDE_LANDING = bytes.fromhex("3b53465b")
_RIDE_SPAN = 400


@dataclass(frozen=True)
class InteractTemplate:
    """A placed-object template that can be interacted with."""

    name: str
    prompt: str  # the interact prompt's English text, "" for none
    ends: tuple[tuple[XYZ, str], ...] = ()  # each LOCATION record: position, zone
    ride: tuple[str, str] | None = None  # a carpet ride's zone, and the arrival point it lands on


@dataclass(frozen=True)
class PlacedObject:
    """An object placed in the zone (``gamedata.bin``'s ``m_objectList``) whose template can be
    interacted with: its zone tag, or the template's name when it has none."""

    tag: str
    at: XYZ
    template: InteractTemplate


def _short_string(data: bytes, at: int) -> str:
    """A string whose length byte is twice its length, as these records store a short one."""
    return data[at + 1 : at + 1 + (data[at] >> 1)].decode("latin-1")


def _locations_in(data: bytes) -> tuple[tuple[XYZ, str], ...]:
    starts = [m.end() for m in re.finditer(re.escape(_LOCATION_NAME), data)]
    out = []
    for at, end in zip(starts, [*starts[1:], len(data)]):
        record = data[at:end]
        if b"LOCATION" not in record:
            continue
        found = [record.find(h) for h in _LOCATION_XYZ]
        if min(found) < 0:
            continue
        x, y, z = (struct.unpack_from("<f", record, k + 4)[0] for k in found)
        k = record.find(_LOCATION_ZONE)
        out.append(((x, y, z), _short_string(record, k + 4) if k >= 0 else ""))
    return tuple(out)


def _ride_in(data: bytes) -> tuple[str, str] | None:
    """The zone and landing a template's InteractCarpetRide names, or None when it has none."""
    start = data.find(_CARPET_RIDE)
    if start < 0:
        return None
    found = [data.find(key, start, start + _RIDE_SPAN) for key in (_RIDE_ZONE, _RIDE_LANDING)]
    if min(found) < 0:
        return None
    zone, landing = (_short_string(data, at + len(_RIDE_ZONE)) for at in found)
    return zone, landing


def parse_interact_template(name: str, data: bytes, lang: Callable[[str, str], str]) -> InteractTemplate | None:
    """A template file's interact prompt, LOCATION records and ride, or None when it has no
    InteractableBehavior. *lang*(file stem, key) is the English text of a ``.lang`` key, or "".
    The prompt is the first key after the behavior that resolves: "Press X to Teleport" from
    GUI.lang, or the object's own words ("Climb Rope", from WizardGameObjects.lang). A quest's
    title is passed over when any other key resolves. With none it stays, as an object left with
    no prompt at all would be taken for travel by its name (Krokotopia's "Teleporter-Static")."""
    at = data.find(_INTERACTABLE)
    if at < 0:
        return None
    found = [
        (k.group(1).decode(), lang(k.group(1).decode(), k.group(2).decode()))
        for k in _PROMPT_KEY.finditer(data, at, at + _PROMPT_SPAN)
    ]
    resolved = [(stem, words) for stem, words in found if words]
    # Quest titles sort last, and are otherwise kept in the file's order.
    resolved.sort(key=lambda row: row[0] == _QUEST_TITLES)
    prompt = resolved[0][1] if resolved else ""
    return InteractTemplate(name, prompt, _locations_in(data), _ride_in(data))


def _travel(placed: Sequence[PlacedObject]) -> list[tuple[str, XYZ]]:
    """(tag, position) of each placed object pressed to travel, by its prompt or, with none, its name."""
    return [
        (obj.tag, obj.at)
        for obj in placed
        if _TRAVEL_PROMPT.search(obj.template.prompt)
        or (not obj.template.prompt and _TRAVEL_NAME.search(obj.template.name))
    ]


def _partners(placed: Sequence[PlacedObject]) -> list[tuple[tuple[str, XYZ], XYZ, bool]]:
    """((tag, position), partner's position, only a bridge) for each object of a two-way pair.
    Among the travel objects: X and X_B (Krokotopia's Forgotten Tombs), X and X-A (Ahmarra's
    "NPC12" and "NPC12-A"), and the only two sharing one zone tag (Dark Water Trench's
    whalebirds and safe currents). And a teleporter's _TOP and _BOTTOM, whatever their prompt
    says ("Press X to Use" in Bale Flotilla).

    *Only a bridge* is true of every pair but X and X_B: it is a link only between two pieces of
    floor, never as a shortcut a walk could do without. The game's files say less of these, and
    none has been pressed where the ends are a walk apart: taking one would trade a walk that
    works for a guess (a shipped bot's in Katzenstein's Lab, to save 2 s)."""
    travel = _travel(placed)
    by_name = {tag.split(" instance")[0]: (tag, at) for tag, at in travel}
    out = []
    for name, here in by_name.items():
        for suffix, only_bridge in (("_B", False), ("-A", True)):
            other = by_name.get(name.removesuffix(suffix)) if name.endswith(suffix) else by_name.get(name + suffix)
            if other is not None:
                out.append((here, other[1], only_bridge))

    sharing: dict[str, list[tuple[str, XYZ]]] = {}
    for tag, at in travel:
        sharing.setdefault(tag, []).append((tag, at))
    for twins in sharing.values():
        if len(twins) == 2:
            first, second = twins
            out += [(first, second[1], True), (second, first[1], True)]

    named = {obj.tag.split(" instance")[0]: obj for obj in placed}
    for name, obj in named.items():
        top = name.removesuffix("_TOP")
        bottom = named.get(top + "_BOTTOM")
        if top != name and bottom is not None and _TRAVEL_NAME.search(obj.template.name):
            out += [((obj.tag, obj.at), bottom.at, True), ((bottom.tag, bottom.at), obj.at, True)]
    return out


def _rides_within(obj: PlacedObject, zone: str, named: Mapping[str, XYZ]) -> bool:
    """Whether *obj* is a carpet ride that sets the wizard down in *zone*. One naming a landing
    that is no arrival point of it leaves it: the Oasis's boat names one on the Sphinx's island."""
    if obj.template.ride is None:
        return False
    where, landing = obj.template.ride
    return where == zone and landing in named


#: Whether two points stand on different pieces of floor.
Crosses = Callable[[XYZ, XYZ], bool]


def interact_candidates(
    zone: str,
    placed: Sequence[PlacedObject],
    named: Mapping[str, XYZ],
    nav: ZoneNav | None,
    crosses: Crosses | None = None,
) -> list[Link]:
    """Each placed object pressed to move the wizard, with where it is taken to land, best source
    first. *crosses*(a, b), when given, says whether two points are on different pieces of floor."""
    points = {name: p for name, p in named.items() if "cinematic" not in name.lower()}
    found: dict[tuple[str, XYZ], Link] = {}  # by tag and place: tags repeat

    # A carpet ride names its landing.
    leaving = set()
    for obj in placed:
        if obj.template.ride is None:
            continue
        if _rides_within(obj, zone, named):
            landing = obj.template.ride[1]
            found[obj.tag, obj.at] = Link(obj.tag, obj.at, landing, named[landing], "ride")
        else:
            leaving.add((obj.tag, obj.at))

    # A ride is shown, then the server sets the wizard down on the arrival point past its far
    # end: ZipLine-3-Quest, pressed live, landed 3 units from "Target-ToMajorIsland", 800 past it.
    for obj in placed:
        ends = [p for p, where in obj.template.ends if where == zone]
        if len(ends) == 2 and math.dist(ends[0][:2], obj.at[:2]) <= NAV_OBJECT_REACH:
            landing, target = f"{obj.template.name}'s far end", ends[1]
            if points:
                nearest = min(points, key=lambda n: math.dist(points[n][:2], ends[1][:2]))
                if math.dist(points[nearest][:2], ends[1][:2]) <= RIDE_LANDING_REACH:
                    landing, target = nearest, points[nearest]
            found.setdefault((obj.tag, obj.at), Link(obj.tag, obj.at, landing, target, "ride"))

    stones = [(obj.tag, obj.at, *kind) for obj in placed if (kind := _stone(obj))]
    hub = [at for _, at, _, back in stones if not back]
    if hub and points:
        center = tuple(sum(c) / len(hub) for c in zip(*hub))
        for tag, at, letter, back in stones:
            partner = center if back else next((p for _, p, kin, ret in stones if kin == letter and ret), None)
            if partner is None:
                continue
            landing = min(points, key=lambda n: math.dist(points[n][:2], partner[:2]))
            found.setdefault((tag, at), Link(tag, at, landing, points[landing], "interact"))

    # A two-way pair (_partners): each lands at the arrival point nearest the other. Nearest in
    # all three axes: a _TOP and its _BOTTOM are thousands of units apart in height and little else.
    if points:
        for (tag, at), other, _ in _partners(placed):
            landing = min(points, key=lambda n: math.dist(points[n], other))
            if math.dist(points[landing], other) <= PAIR_LANDING_REACH:
                found.setdefault((tag, at), Link(tag, at, landing, points[landing], "interact"))

    travel = _travel(placed)
    if nav is not None:
        for (tag, at), (landing, target) in nav_links(nav, travel, points, crosses).items():
            found.setdefault((tag, at), Link(tag, at, landing, target, "interact"))
    # A ride out of the zone is no link by any rule above.
    return [link for key, link in found.items() if key not in leaving]


def nav_links(
    nav: ZoneNav,
    sources: Sequence[tuple[str, XYZ]],
    points: Mapping[str, XYZ],
    crosses: Crosses | None = None,
) -> dict[tuple[str, XYZ], tuple[str, XYZ]]:
    """For each (name, position) of *sources* that a zone.nav link edge starts near, the best
    such edge's landing: the arrival point nearest its far end, or the far end itself. Best is,
    in turn: landing on another piece of floor, by *crosses*(source, landing), as an edge whose
    ends share a floor is a walk, not a link (Arcanum's lift foot starts one to the other lift's
    foot); starting nearest, to within 100; then shortest (the top of Mirage's Ladder 06 starts
    both its 731-unit climb down and a 7,004-unit edge across the zone)."""
    edges = []
    for a, b in nav.link_edges():
        near, far = nav.nodes[a], nav.nodes[b]
        for name, at in sources:
            reach = math.dist(at, near)
            if reach > NAV_OBJECT_REACH:
                continue
            landing, target = f"zone.nav node {b}", far
            if points:
                nearest = min(points, key=lambda n: math.dist(points[n], far))
                if math.dist(points[nearest], far) <= NAV_OBJECT_REACH:
                    landing, target = nearest, points[nearest]
            walk = crosses is not None and not crosses(at, target)
            edges.append((walk, round(reach / 100), math.dist(near, far), name, at, landing, target))
    out = {}
    for *_, name, at, landing, target in sorted(edges, key=lambda e: e[:3]):
        out.setdefault((name, at), (landing, target))
    return out


def zone_links(
    zone: str,
    grid: Grid,
    passable: np.ndarray,
    label: np.ndarray,
    volumes: Sequence[Volume],
    placed: Sequence[PlacedObject],
    named: Mapping[str, XYZ],
    nav: ZoneNav | None,
    teleports: frozenset[str] = frozenset(),
) -> list[Link]:
    """The zone's links: each volume that joins one piece of its floor to another, and each
    object pressed to move the wizard that joins pieces or is a shortcut worth its press.

    A volume link must join two pieces of floor, which alone throws out the zone's exits, whose
    names match an arrival point on their own piece ("Street4 to Cavern"). A volume whose name
    pairs it with no landing on another piece (Lemuria's tower lifts, each named for the end it
    stands at) takes its landing from zone.nav instead, if its name says it moves the wizard or
    it is one of *teleports*, the volumes a teleporting trigger fires on
    (:func:`teleport_volumes`): Crimson Fields' passage north is an "Activator Volume" that
    carries the wizard 20,000 units. An object standing at a volume is the pad drawn there
    (Triton's Telepapads), not a link of its own. *label* is each passable spot's piece, from
    :func:`~bartleby.runtime.movement.zone_grid.pieces`."""
    by_name = link_candidates(volumes, named)
    every_volume = [(v.name, tuple(v.center)) for v in volumes if _LINK_VOLUME.search(v.name)]
    # A pair's end or a ride that names its landing is hidden only by a volume that teleports or
    # says it does: a quest's activator at it (Eastern Sea's traveler stands in one) is no pad.
    true_pads = [(v.name, tuple(v.center)) for v in volumes if _PAD_VOLUME.search(v.name) or v.name in teleports]
    pairs = _partners(placed)
    certain = {here for here, _, _ in pairs}
    only_bridges = {here for here, _, only_bridge in pairs if only_bridge}
    certain |= {(obj.tag, obj.at) for obj in placed if _rides_within(obj, zone, named)}
    moving = [
        (v.name, tuple(v.center))
        for v in volumes
        if (_LINK_VOLUME.search(v.name) and _MOVING_VOLUME.search(v.name)) or v.name in teleports
    ]

    def ends(link: Link):
        here, there = nearest_node(grid, passable, *link.source), nearest_node(grid, passable, *link.target)
        if here is None or there is None or math.dist(grid.world(*here)[:2], link.source[:2]) > link.reach + grid.cell:
            return None
        return here, there

    def crosses(a, b) -> bool:
        here, there = nearest_node(grid, passable, *a), nearest_node(grid, passable, *b)
        return here is not None and there is not None and label[here] != label[there]

    out = []
    joined = set()
    for link in by_name:
        found = ends(link)
        if found and label[found[0]] != label[found[1]]:
            out.append(link)
            joined.add(link.name)
    if nav is not None:
        points = {n: p for n, p in named.items() if "cinematic" not in n.lower()}
        unpaired = [(n, p) for n, p in moving if n not in joined and "cinematic" not in n.lower()]
        for (name, at), (landing, target) in nav_links(nav, unpaired, points, crosses).items():
            link = Link(name, at, landing, target)
            found = ends(link)
            if found and label[found[0]] != label[found[1]]:
                out.append(link)
    for link in interact_candidates(zone, placed, named, nav, crosses):
        found = ends(link)
        pads = true_pads if (link.name, link.source) in certain else every_volume
        if found is None or any(math.dist(link.source[:2], pad[:2]) <= PAD_REACH for _, pad in pads):
            continue
        shortcut = (link.name, link.source) not in only_bridges and math.dist(link.source, link.target) >= SHORTCUT_MIN
        if label[found[0]] != label[found[1]] or shortcut:
            out.append(link)
    return out


def zone_exits(volumes: Sequence[Volume], teleports: frozenset[str], links: Sequence[Link]) -> list[Volume]:
    """The volumes that teleport a wizard out of the zone: every one a teleporting trigger fires
    on (*teleports*, from :func:`teleport_volumes`) that is not one of the zone's volume links."""
    linked = {(link.name, link.source) for link in links if link.kind == "volume"}
    return [v for v in volumes if v.name in teleports and (v.name, tuple(v.center)) not in linked]
