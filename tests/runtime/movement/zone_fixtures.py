"""Zones built in code for the planner's tests: floors, solids, and the bytes of each file.

Nothing of the game's is here. Each ``*_bytes`` writes the layout its parser reads, as core's
``docs/WALKING.md`` describes it, so a test can check the parser and the planner on a zone of
its own making.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

from bartleby.runtime.movement.collision import BOX, CYLINDER, MESH, PARAM_FORMATS, SPHERE, WALKABLE, Solid

IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)

XYZ = tuple[float, float, float]


def floor(x0: float, y0: float, x1: float, y1: float, z0: float, z1: float | None = None) -> list[XYZ]:
    """A rectangle of floor as two triangles' corners; with *z1*, a ramp rising from z0 at x0 to z1 at x1."""
    z1 = z0 if z1 is None else z1
    a, b, c, d = (x0, y0, z0), (x1, y0, z1), (x1, y1, z1), (x0, y1, z0)
    return [a, b, c, a, c, d]


def mesh(*floors: list[XYZ], category: int = WALKABLE) -> Solid:
    """One walkable mesh holding every triangle of *floors*."""
    vertices = [p for f in floors for p in f]
    faces = [(k, k + 1, k + 2) for k in range(0, len(vertices), 3)]
    return Solid(MESH, category, IDENTITY, (0.0, 0.0, 0.0), 1.0, (), vertices, faces)


def box(center: XYZ, length: float, width: float, depth: float, category: int = 0) -> Solid:
    return Solid(BOX, category, IDENTITY, center, 1.0, (length, width, depth))


def cylinder(center: XYZ, length: float, radius: float, category: int = 0) -> Solid:
    """A standing cylinder, stored as the game stores one: (length, radius)."""
    return Solid(CYLINDER, category, IDENTITY, center, 1.0, (length, radius))


def sphere(center: XYZ, radius: float, category: int = 0) -> Solid:
    return Solid(SPHERE, category, IDENTITY, center, 1.0, (radius,))


def _string(text: str) -> bytes:
    data = text.encode()
    return struct.pack("<i", len(data)) + data


def collision_bytes(solids: Sequence[Solid]) -> bytes:
    """A ``collision.bcd`` holding *solids*."""
    out = [struct.pack("<i", len(solids))]
    for s in solids:
        out.append(struct.pack("<iII", s.proxy, s.category, 0))
        if s.proxy == MESH:
            out.append(struct.pack("<ii", len(s.vertices), len(s.faces)))
            out += [struct.pack("<fff", *v) for v in s.vertices]
            out += [struct.pack("<iii", *f) + struct.pack("<fff", 0, 0, 1) for f in s.faces]
        out.append(_string("solid"))
        out.append(struct.pack("<9f", *s.rotation))
        out.append(struct.pack("<fff", *s.location))
        out.append(struct.pack("<f", s.scale))
        out.append(_string("material"))
        out.append(struct.pack("<i", s.proxy))
        if PARAM_FORMATS[s.proxy]:
            out.append(struct.pack(PARAM_FORMATS[s.proxy], *s.params))
    return b"".join(out)


def _short(text: str) -> bytes:
    """A string as property records hold one: a length byte that is twice its length, then the text."""
    return bytes([2 * len(text)]) + text.encode("latin-1")


def _record(key: str, value: bytes) -> bytes:
    """A property record: its size in bits, its hash, its value."""
    return struct.pack("<I", (8 + len(value)) * 8) + bytes.fromhex(key) + value


def _float(key: str, value: float) -> bytes:
    return _record(key, struct.pack("<f", value))


def volumes_bytes(volumes: Sequence[tuple]) -> bytes:
    """A ``volumes.xml`` of (name, center, shape, radius, length, width, height) rows; shape
    None writes no shape record, as the game does for most spheres."""
    out = [b"BINd" + bytes(8)]
    for name, (x, y, z), shape, radius, length, width, height in volumes:
        out.append(bytes(16))
        out.append(_record("8b04e6c6", _short(name)))
        out += [_float("28f8b37d", x), _float("29f8b37d", y), _float("2af8b37d", z)]
        out.append(_record("01341840", bytes(8)))
        if shape is not None:
            out.append(_record("ccb28789", _short(shape)))
        out.append(_float("df33f93a", radius))
        if shape is not None:
            out += [_float("3915482d", length), _float("97f5eb35", width), _float("8c259234", height)]
        out.append(struct.pack("<I", 65) + bytes(8))  # whatever follows: not a size record
    return b"".join(out)


#: ResTeleport's class hash, which a teleporting trigger's results hold.
RES_TELEPORT = struct.pack("<I", 0x0DA3207D)


def triggers_bytes(triggers: Sequence[tuple[str, Sequence[str], bool]]) -> bytes:
    """A ``triggers.xml`` of (name, events, teleports) rows: each event "Enter_<volume>" or
    "Exit_<volume>", and *teleports* adding a ResTeleport result."""
    out = [b"BINd" + bytes(8)]
    for name, events, teleports in triggers:
        out.append(_record("100cc9b8", _short(name)))
        out.append(bytes(12))
        for event in events:
            out.append(b"\x00" + _short(event) + b"H\x00\x00\x00")
        if teleports:
            out.append(bytes(4) + RES_TELEPORT + bytes(8))
        out.append(bytes(20))
    return b"".join(out)


def zone_nav_bytes(nodes: Sequence[XYZ], pairs: Sequence[tuple[int, int]]) -> bytes:
    """A ``zone.nav`` of *nodes* and the edges *pairs*."""
    out = [struct.pack("<HHH", len(nodes) - 1, len(nodes), 0)]
    out += [struct.pack("<fffH", *node, k) for k, node in enumerate(nodes)]
    out.append(struct.pack("<I", len(pairs)))
    out += [struct.pack("<HH", a, b) for a, b in pairs]
    return b"".join(out)


def template_bytes(prompt_key: str | None, locations: Sequence[tuple[XYZ, str]] = ()) -> bytes:
    """A template file with an InteractableBehavior, its prompt's ``.lang`` key (``GUI_00000001``
    style), and a LOCATION record per (position, zone)."""
    out = [b"BINd" + bytes(8), b"\x28InteractableBehavior", bytes(6)]
    if prompt_key:
        out.append(_short(prompt_key))
    for (x, y, z), zone in locations:
        out.append(bytes.fromhex("cbdec46e") + _short("LOCATION"))
        out += [
            bytes.fromhex(h) + struct.pack("<f", v) for h, v in zip(("bffe5a5a", "c0fe5a5a", "c1fe5a5a"), (x, y, z))
        ]
        out.append(bytes.fromhex("c122a8ba") + _short(zone))
    return b"".join(out)
