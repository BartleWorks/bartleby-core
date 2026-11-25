"""A zone's floor and walls, read from its ``collision.bcd``.

The floor is every mesh flagged walkable; the walls are its boxes, cylinders and spheres. The
parser follows Deimos' ``src/collision.py`` (PeechezNCreem, ISC), and the rule for when a solid
blocks a foot comes from Deimos' ``src/collision_math.py`` (GPL-3, as this library is). Moved
from BartleBot's ``tools/probe_walk_route.py``; core's ``docs/WALKING.md``, *The floor*, has
what each rule was measured against.

A placed object's own solids (its template's ``CollisionBehavior`` names a file of them, plain
XML) are read by :func:`parse_solid_xml`, put where the object stands by :func:`place_solids`,
and walled by :func:`placed_footprints`; ``gates.py`` says what the planner does with them.

Nothing here opens a file: :func:`parse_collision` and :func:`parse_solid_xml` take the file's
bytes, which a tool reads out of the game's wads.
"""

from __future__ import annotations

import math
import struct
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, replace

import numpy as np

# Collision flags (Deimos' CollisionFlag).
WALKABLE = 1 << 1
OBJECT = 1 << 0
NON_MOVEMENT = (1 << 7) | (1 << 8) | (1 << 3) | (1 << 9)  # client object, trigger, hitscan, fog
BOX, SPHERE, CYLINDER, MESH = 0, 2, 3, 6

# When a solid walls off a foot at ground height z (Deimos' _collider_blocks_foot): a solid
# whose base is at or below the foot blocks once it rises Z_BOX_TOL above it; Deimos lets a
# cylinder or sphere rise Z_EMBED_TOL, as a big one is often the coarse bounds of a whole
# structure whose floor lies inside it. Only one of radius ROUND_SHELL_MIN or more is given
# that: Mirage's hub has a rock, a cylinder of radius 143 rising 56 above the street, that the
# planner walked through and a wizard cannot (it slid around holding 183 from its center). The
# probe's notes said "400 across", but its code, which every route walked since was planned
# with, compared the radius; so does this.
Z_BAND_UP = 50.0
Z_BAND_DOWN = 8.0
Z_EMBED_TOL = 75.0
Z_BOX_TOL = 20.0
ROUND_SHELL_MIN = 400.0

# A triangle steeper than this is a cliff face, not floor.
MIN_FLOOR_NORMAL_Z = 0.5


@dataclass
class Solid:
    """One shape of the file: a mesh (with its vertices and faces) or a primitive."""

    proxy: int
    category: int
    rotation: tuple[float, ...]
    location: tuple[float, float, float]
    scale: float
    params: tuple[float, ...]
    vertices: list[tuple[float, float, float]] | None = None
    faces: list[tuple[int, int, int]] | None = None


class _Stream:
    def __init__(self, data: bytes) -> None:
        self.data, self.pos = data, 0

    def unpack(self, fmt: str) -> tuple:
        size = struct.calcsize(fmt)
        out = struct.unpack_from(fmt, self.data, self.pos)
        self.pos += size
        return out

    def skip_string(self) -> None:
        (length,) = self.unpack("<i")
        self.pos += length


#: Each proxy's parameters after the shape record: a box's three sides, a sphere's radius, a
#: cylinder's (length, radius), and none for a mesh.
PARAM_FORMATS = {0: "<fff", 1: "<fff", 2: "<f", 3: "<ff", 4: "<ff", 5: "<ffff", 6: ""}


def parse_collision(data: bytes) -> list[Solid]:
    """Every shape in a ``collision.bcd``."""
    stream = _Stream(data)
    (count,) = stream.unpack("<i")
    solids = []
    for _ in range(count):
        proxy, category, _collide = stream.unpack("<iII")
        vertices = faces = None
        if proxy == MESH:
            vertex_count, face_count = stream.unpack("<ii")
            vertices = [stream.unpack("<fff") for _ in range(vertex_count)]
            faces = []
            for _ in range(face_count):
                faces.append(stream.unpack("<iii"))
                stream.unpack("<fff")  # the face normal; recomputed from the vertices
        stream.skip_string()  # name
        rotation = stream.unpack("<9f")
        location = stream.unpack("<fff")
        (scale,) = stream.unpack("<f")
        stream.skip_string()  # material
        (proxy_again,) = stream.unpack("<i")
        params = stream.unpack(PARAM_FORMATS[proxy_again]) if PARAM_FORMATS[proxy_again] else ()
        solids.append(Solid(proxy, category, rotation, location, scale, params, vertices, faces))
    return solids


def _to_world(points: np.ndarray, solid: Solid) -> np.ndarray:
    # Deimos' toMultidim + transformCube: the stored matrix is applied transposed.
    m = np.asarray(solid.rotation, dtype=float).reshape(3, 3).T
    return points @ m + np.asarray(solid.location, dtype=float)


def walkable_triangles(solids: list[Solid]) -> np.ndarray:
    """Every floor triangle in world space, (N, 3, 3): the walkable meshes' faces, cliffs left out."""
    chunks = []
    for s in solids:
        if s.proxy == MESH and s.category & WALKABLE and s.faces:
            world = _to_world(np.asarray(s.vertices, dtype=float), s)
            chunks.append(world[np.asarray(s.faces, dtype=np.intp)])
    tris = np.concatenate(chunks) if chunks else np.empty((0, 3, 3))
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    lengths = np.linalg.norm(normals, axis=1)
    keep = lengths > 1e-6
    tris, normals, lengths = tris[keep], normals[keep], lengths[keep]
    return tris[np.abs(normals[:, 2]) / lengths >= MIN_FLOOR_NORMAL_Z]


@dataclass
class Footprint:
    """Where a solid stands on the floor plan, and the heights it spans."""

    hull: np.ndarray | None  # convex polygon, CCW, or None for a circle
    center: tuple[float, float]
    radius: float
    zlo: float
    zhi: float
    firm: bool  # blocks once it rises Z_BOX_TOL above the foot, not Z_EMBED_TOL


def _convex_hull(points: np.ndarray) -> np.ndarray:
    pts = sorted(set(map(tuple, points.tolist())))
    if len(pts) <= 2:
        return np.asarray(pts)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.asarray(lower[:-1] + upper[:-1])


def blocking_footprints(solids: list[Solid]) -> list[Footprint]:
    """The footprint of every box, sphere and cylinder a wizard cannot walk through."""
    out = []
    for s in solids:
        if s.category & NON_MOVEMENT and not s.category & OBJECT:
            continue
        if s.proxy == BOX:
            length, width, depth = (v / 2 for v in s.params)
            corners = np.array([(x, y, z) for x in (-length, length) for y in (-width, width) for z in (-depth, depth)])
            world = _to_world(corners, s)
            hull = _convex_hull(world[:, :2])
            if len(hull) < 3:
                continue
            out.append(Footprint(hull, (0.0, 0.0), 0.0, world[:, 2].min(), world[:, 2].max(), True))
        elif s.proxy == SPHERE:
            r = half = s.params[0] * s.scale
        elif s.proxy == CYLINDER:
            # A cylinder stores (length, radius), not the (radius, length) Deimos reads. Walked
            # in Unicorn Way: a wizard slid along the tree at (-3606, 6581), stored (264.9, 85.7),
            # holding exactly 135 units from its center, which is radius 85.7 plus the wizard's
            # own 49. Read the other way it is a 265-wide disc the wizard stood inside of.
            half, r = s.params[0] / 2 * s.scale, s.params[1] * s.scale
        else:
            continue
        if s.proxy != BOX and r > 0:
            x, y, z = s.location
            out.append(Footprint(None, (x, y), r, z - half, z + half, r < ROUND_SHELL_MIN))
    return out


# --- a placed object's solids --------------------------------------------------------------------


def _floats(text: str | None) -> tuple[float, ...]:
    return tuple(float(v) for v in (text or "").split())


def parse_solid_xml(data: bytes) -> list[Solid]:
    """Every primitive of a placed object's solid file, in the object's own frame: a ``<world>``
    of ``<primitive type="box|cylinder|sphere|mesh">``, each with its ``<location>`` and
    ``<rotation matrix>``. A box's ``<dimensions l w d>`` are its full sides along x, y and z, a
    cylinder's ``<cylinder radius length>`` stand along z, and a mesh lists its vertices and
    faces. A mesh that names another file (``<mesh source>``) and anything else is skipped. The
    category is not kept: the solid files tag their blockers ``CT_ClientObject``, which in a
    ``collision.bcd`` marks a solid that walls nobody, and here walls the wizard."""
    root = ElementTree.fromstring(data.decode("utf-8-sig"))
    out = []
    for prim in root.iter("primitive"):
        kind = prim.get("type")
        loc = prim.find("location")
        location = tuple(float(loc.get(k, 0.0)) for k in "xyz") if loc is not None else (0.0, 0.0, 0.0)
        rot = prim.find("rotation")
        rotation = _floats(rot.get("matrix")) if rot is not None else ()
        if len(rotation) != 9:
            rotation = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
        if kind == "box" and (dims := prim.find("dimensions")) is not None:
            params = tuple(float(dims.get(k, 0.0)) for k in ("l", "w", "d"))
            out.append(Solid(BOX, 0, rotation, location, 1.0, params))
        elif kind == "cylinder" and (cyl := prim.find("cylinder")) is not None:
            # (length, radius), as collision.bcd stores a cylinder.
            params = (float(cyl.get("length", 0.0)), float(cyl.get("radius", 0.0)))
            out.append(Solid(CYLINDER, 0, rotation, location, 1.0, params))
        elif kind == "sphere" and (rad := prim.find("radius")) is not None:
            out.append(Solid(SPHERE, 0, rotation, location, 1.0, (float(rad.get("value", 0.0)),)))
        elif kind == "mesh" and (body := prim.find("mesh")) is not None:
            vertices = [tuple(float(v.get(k, 0.0)) for k in "xyz") for v in body.iter("vert")]
            faces = [tuple(int(f.get(k, 0)) for k in "abc") for f in body.iter("face")]
            if vertices:
                out.append(Solid(MESH, 0, rotation, location, 1.0, (), vertices, faces))
    return out


def object_rotation(orientation: tuple[float, float, float]) -> np.ndarray:
    """The 3x3 matrix that turns a placed object's frame into the world's, from its
    ``m_orientation`` (pitch, roll, yaw), applied as :func:`_to_world` applies a stored one.

    A yaw turns the object the way a wizard's yaw turns the wizard (``geometry.facing``): its
    own -y comes to face where a wizard with that yaw walks. Checked on the shipped zones'
    gates turned off the axes, which fill the gaps in their walls turned this way and lie across
    open floor turned the other (``docs/WALKING.md``, *Gates*). Pitch and roll, which 61 of the
    3,163 placed solids have, are applied about x and then y before the yaw, by a convention
    nothing has checked."""
    pitch, roll, yaw = orientation
    cx, sx = math.cos(pitch), math.sin(pitch)
    cy, sy = math.cos(roll), math.sin(roll)
    cz, sz = math.cos(-yaw), math.sin(-yaw)
    rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return rz @ ry @ rx


def place_solids(
    solids: list[Solid], location: tuple[float, float, float], orientation: tuple[float, float, float], scale: float
) -> list[Solid]:
    """*solids*, in an object's frame, put where the object stands: turned by its orientation,
    scaled, and moved to its location. The scale is folded into each solid's sizes and
    vertices, so every solid returned has scale 1."""
    turn = object_rotation(orientation)
    at = np.asarray(location, dtype=float)
    out = []
    for s in solids:
        own = np.asarray(s.rotation, dtype=float).reshape(3, 3)
        rotation = tuple((turn @ own).ravel().tolist())
        center = turn @ (np.asarray(s.location, dtype=float) * scale) + at
        size = scale * s.scale
        vertices = None if s.vertices is None else [tuple(float(c) * size for c in v) for v in s.vertices]
        params = tuple(p * size for p in s.params)
        out.append(
            replace(s, rotation=rotation, location=tuple(center.tolist()), scale=1.0, params=params, vertices=vertices)
        )
    return out


def placed_footprints(solids: list[Solid]) -> list[Footprint]:
    """Where a placed object's solids stand on the floor plan: as :func:`blocking_footprints`
    finds a box, a sphere and a cylinder (a cylinder taken as standing), whatever their
    category, and a mesh as the hull of its vertices, firm, from its lowest point to its highest."""
    out = blocking_footprints([replace(s, category=0) for s in solids if s.proxy != MESH])
    for s in solids:
        if s.proxy == MESH and s.vertices:
            world = _to_world(np.asarray(s.vertices, dtype=float), s)
            hull = _convex_hull(world[:, :2])
            if len(hull) >= 3:
                out.append(Footprint(hull, (0.0, 0.0), 0.0, float(world[:, 2].min()), float(world[:, 2].max()), True))
    return out
