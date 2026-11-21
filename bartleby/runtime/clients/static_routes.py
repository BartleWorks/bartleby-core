"""Static routes: from a slot in the game's image to the config nodes and to the key bindings.

The resizer's config nodes (``client_resizer.find_config_nodes``) and the key map's
``InputBindingList`` (``game_keys.find_binding_list``) were each found by scanning the client's
whole heap, a pass of about 2 s for the nodes and three of 4.5 to 6.2 s for the list. Each also
hangs off an object whose constructor stores ``this`` in a static slot of
``WizardGraphicalClient.exe``, so a short chain of reads from that slot reaches it, as
wizwalker's patterns reach the video manager and GameClient. Core's
``tools/probe_static_paths.py`` found both chains on the live realm's r814353 (2026-11-20), and
its RESULTS have the climb from each target to its slot, the code patterns and what held on
four clients:

    the config     [slot] +0x80 the std::map of sections, "VideoSettings" in it, its node's
                   +0x40 a vector of one group, whose children sit inline in its own vector,
                   0x78 bytes each, named at +0x50: about 12 reads, 0.3 to 0.9 ms
    the bindings   [slot] +0xB0, a SharedPointer<InputBindingList>: 2 reads and the list's
                   class, 0.2 ms

**This module follows a chain; its owner checks the end.** The resizer checks the two nodes it
is handed as its heap scan checks the ones it finds, and the key map checks the list's class,
so a chain that reaches something else is a miss like any other. Each finder tries its route
first and falls back to its heap scan, unchanged, on a miss.

**The slot is found by a pattern, once per process.** Each route has a tuple of patterns, as
wizwalker's ``HookHandler.AUTOBOT_PATTERNS``, tried in turn: the first to match exactly once in
the module gives the slot, through its RIP-relative disp32, the pattern's one group. A tuple, so
a pattern for the game's other compiler can be added beside the old one's; none has been
written, since no build of the new compiler (r811339, r813697) was at hand. The answer, a slot
or no single match, is kept for the client, since its image doesn't change; a scan that raised
is tried again next time.

**A miss falls back, says why, and says so once.** Every miss has a reason (:class:`Missed`, or
a read that raised), traced on the route's span as ``outcome="heap"`` and ``reason``, and
logged at INFO once per client and route: the heap still finds the object, so nothing a player
sees changes but a few seconds, while a WARNING is kept for what turns a feature off. The
session log, which keeps INFO, then shows a game patch that broke a route on its first day.

**Traced.** Each attempt is a span, ``config.route`` or ``key_map.route``, inside its caller's,
with its ``outcome``, ``route`` or ``heap``, and the ``reason`` for a miss; the attempt that
scanned the module for the slot has the scan's seconds as ``module_scan``.
"""

from __future__ import annotations

import logging
import re
import struct
import time
import weakref
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from wizwalker.errors import PatternFailed

from bartleby.runtime import run_trace

logger = logging.getLogger(__name__)

MODULE = "WizardGraphicalClient.exe"

# The config object (0xC8 bytes): two std::strings, then a std::map<std::string, Section>
# whose head node it holds at +0x80. 64 of the code's loads of its slot go on to +0x80.
CONFIG_SECTIONS = 0x80
VIDEO_SETTINGS = b"VideoSettings"
# An MSVC std::map node: three links, the head's isnil byte, then the key and the value.
TREE_LEFT, TREE_PARENT, TREE_RIGHT, TREE_ISNIL, TREE_KEY, TREE_VALUE = 0x0, 0x8, 0x10, 0x19, 0x20, 0x40
MAX_TREE_DEPTH = 64
# A section: a std::vector<Group*> first. A group: its parent section at +0x10, its children
# inline in a vector of its own from +0x20, their count at +0x38.
SECTION_GROUPS = 0x0
GROUP_SECTION, GROUP_CHILDREN, GROUP_COUNT = 0x10, 0x20, 0x38
CHILD_STRIDE, CHILD_NAME = 0x78, 0x50
MAX_GROUPS, MAX_CHILDREN = 16, 1024
MAX_STRING = 0x400

# The bindings holder, an object with no reflected type: its SharedPointer<InputBindingList>.
HOLDER_BINDINGS = 0xB0

# A pointer below this is none: null, or a small integer where a pointer was expected.
_LOWEST = 0x10000
# How much of the code at a match is read to find its group in: more than any pattern's length.
_MATCH_WINDOW = 0x40


@dataclass(frozen=True)
class Route:
    """One static route: what it reaches, and the patterns that find its slot."""

    name: str
    """For the trace's span and the log: ``config`` or ``key_map``."""
    what: str
    """What it reaches, for the log."""
    patterns: tuple[bytes, ...]
    """Each a regex with one group, the disp32 of a RIP-relative operand whose instruction ends with it.
    Tried in turn; the first to match exactly once gives the slot."""

    @property
    def span(self) -> str:
        return f"{self.name}.route"


#: The slot the config object's constructor stores ``this`` in, ``[module + 0x33BD0B0]`` on r814353.
CONFIG = Route(
    "config",
    "the config nodes",
    (
        # The old compiler. Its constructor's store: mov byte [rdi+?], 0; mov [rip+slot], rdi; jmp;
        # mov rdi, rbx; mov [rsi+?], rdi.
        rb"\xC6\x87....\x00\x48\x89\x3D(....)\xEB.\x48\x8B\xFB\x48\x89\xBE....",
        # The old compiler. Its destructor's clear, before the delete of its 0xC8 bytes.
        rb"\x48\x8B\x9F....\x48\x85\xDB\x74.\x4C\x89\x35(....)\x48\x8B\xCB\xE8....\xBA\xC8\x00\x00\x00",
    ),
)

#: The slot the bindings holder's constructor stores ``this`` in, ``[module + 0x33BD518]`` on r814353.
BINDINGS = Route(
    "key_map",
    "the key bindings",
    (
        # The old compiler: mov rdi, rcx; mov [rip+slot], rcx; call; nop; lea rax, [vtable]; mov [rdi], rax.
        rb"\x48\x8B\xF9\x48\x89\x0D(....)\xE8....\x90\x48\x8D\x05....\x48\x89\x07",
    ),
)


class Missed(Exception):
    """A route reached nothing it could use; ``reason`` says where it stopped, in a few words."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _State:
    """What one client's routes have learned: each route's slot, or None for no single match."""

    slots: dict[str, int | None] = field(default_factory=dict)
    logged: set[str] = field(default_factory=set)


_states: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _state(reader) -> _State:
    """*reader*'s state, kept as long as the reader is: one per client, so one per process."""
    try:
        return _states.setdefault(reader, _State())
    except TypeError:  # a reader that can't be weakly referred to keeps nothing
        return _State()


async def _u64(reader, address: int) -> int:
    return struct.unpack("<Q", await reader.read_bytes(address, 8))[0]


async def _pointer(reader, address: int, missing: str) -> int:
    """The pointer at *address*, or :class:`Missed` with *missing* for one that is null."""
    value = await _u64(reader, address)
    if value < _LOWEST:
        raise Missed(missing)
    return value


def resolve(pattern: bytes, code: bytes, at: int) -> int | None:
    """Where the RIP-relative operand of *pattern*'s match at the start of *code* points, or None.

    *code* is read from *at*. The match's one group is the disp32, and the instruction ends with
    it, so the target is the group's end plus the disp32.
    """
    match = re.match(pattern, code, re.DOTALL)
    if match is None:
        return None
    return at + match.end(1) + struct.unpack_from("<i", code, match.start(1))[0]


async def _find_slot(reader, route: Route) -> int | None:
    """The slot the first of *route*'s patterns to match exactly once points at, or None."""
    for pattern in route.patterns:
        try:
            found = await reader.pattern_scan(pattern, module=MODULE, return_multiple=True)
        except PatternFailed:
            continue
        if len(found) == 1:
            slot = resolve(pattern, await reader.read_bytes(found[0], _MATCH_WINDOW), found[0])
            if slot is not None:
                return slot
    return None


async def slot(reader, route: Route) -> int:
    """*route*'s slot in *reader*'s module: scanned for once per client, then kept.

    Raises :class:`Missed` when no pattern matches exactly once, and what the scan raises.
    """
    state = _state(reader)
    if route.name not in state.slots:
        began = time.perf_counter()
        state.slots[route.name] = await _find_slot(reader, route)
        run_trace.note(module_scan=round(time.perf_counter() - began, 4))
    found = state.slots[route.name]
    if found is None:
        raise Missed("no single pattern match")
    return found


async def follow[T](reader, route: Route, chain: Callable[[object, int], Awaitable[T]]) -> T | None:
    """What *chain* reaches from *route*'s slot, or None, the reason traced and logged once per client.

    *chain* is handed the reader and the slot's address, and returns what it reached, checked,
    or raises :class:`Missed` naming where it stopped. A read that raises is a miss too. A
    cancel is not caught.
    """
    with run_trace.span(route.span):
        try:
            found = await chain(reader, await slot(reader, route))
        except Missed as missed:
            reason = missed.reason
        except Exception as exc:  # noqa: BLE001 - a read that failed is a miss, and the heap's to try
            reason = f"a read failed ({type(exc).__name__})"
        else:
            run_trace.note(outcome="route")
            return found
        run_trace.note(outcome="heap", reason=reason)
        _fell_back(reader, route, reason)
        return None


def _fell_back(reader, route: Route, reason: str) -> None:
    state = _state(reader)
    if route.name in state.logged:
        return
    state.logged.add(route.name)
    process = getattr(getattr(reader, "process", None), "process_id", None)
    logger.info(
        "The static route to %s missed in process %s (%s); scanning its memory instead", route.what, process, reason
    )


# -- the config's chain ---------------------------------------------------------------------------


async def std_string(reader, address: int) -> bytes:
    """An MSVC std::string's bytes: inline up to 15, else through a pointer; then its size and capacity."""
    raw = await reader.read_bytes(address, 0x20)
    size, capacity = struct.unpack_from("<QQ", raw, 0x10)
    if size > MAX_STRING:
        raise Missed("a key too long")
    return raw[:size] if capacity < 0x10 else await reader.read_bytes(struct.unpack_from("<Q", raw)[0], size)


async def map_find(reader, head: int, key: bytes) -> int | None:
    """The node of an MSVC std::map<std::string, ...> holding *key*, by its head node, or None.

    std::less<std::string> orders keys as bytes compare in Python: unsigned, then the shorter first.
    """
    node = await _u64(reader, head + TREE_PARENT)  # the head's parent is the root
    for _ in range(MAX_TREE_DEPTH):
        raw = await reader.read_bytes(node, TREE_KEY)
        if raw[TREE_ISNIL]:
            return None
        here = await std_string(reader, node + TREE_KEY)
        if here == key:
            return node
        node = struct.unpack_from("<Q", raw, TREE_LEFT if key < here else TREE_RIGHT)[0]
    return None


async def group_children(reader, group: int) -> dict[bytes, int]:
    """A config group's children by name: they sit inline in its vector, CHILD_STRIDE bytes apart."""
    begin, end = struct.unpack("<QQ", await reader.read_bytes(group + GROUP_CHILDREN, 16))
    count = (end - begin) // CHILD_STRIDE
    if not 0 < count <= MAX_CHILDREN or (end - begin) % CHILD_STRIDE:
        return {}
    raw = await reader.read_bytes(begin, end - begin)
    children = {}
    for index in range(count):
        name = raw[index * CHILD_STRIDE + CHILD_NAME : (index + 1) * CHILD_STRIDE].split(b"\x00")[0]
        children[name] = begin + index * CHILD_STRIDE
    return children


@dataclass(frozen=True)
class ConfigGroup:
    """A group of the VideoSettings section holding both names: the two children, by address."""

    group: int
    ui_size: int
    resolution: int


async def config_groups(reader, config_slot: int) -> list[ConfigGroup]:
    """Each group of the config's VideoSettings section with a UISize and a Resolution, through *config_slot*.

    One on every client the probe read. Raises :class:`Missed` where the chain stops; which of
    them is live is for the caller to check.
    """
    config = await _pointer(reader, config_slot, "the slot is empty")
    head = await _pointer(reader, config + CONFIG_SECTIONS, "the config has no sections")
    node = await map_find(reader, head, VIDEO_SETTINGS)
    if node is None:
        raise Missed("no VideoSettings section")
    begin, end = struct.unpack("<QQ", await reader.read_bytes(node + TREE_VALUE + SECTION_GROUPS, 16))
    if not 0 < end - begin <= 8 * MAX_GROUPS or (end - begin) % 8:
        raise Missed("no groups in VideoSettings")
    groups = []
    for at in range(begin, end, 8):
        group = await _u64(reader, at)
        if group < _LOWEST:
            continue
        children = await group_children(reader, group)
        ui_size, resolution = children.get(b"UISize"), children.get(b"Resolution")
        if ui_size is not None and resolution is not None:
            groups.append(ConfigGroup(group, ui_size, resolution))
    if not groups:
        raise Missed("no UISize and Resolution in VideoSettings")
    return groups


# -- the bindings' chain ----------------------------------------------------------------------------


async def held_binding_list(reader, bindings_slot: int) -> int:
    """What the bindings holder's shared pointer holds, through *bindings_slot*: the account's list.

    Raises :class:`Missed` where the chain stops. Whether it is a list is for the caller to
    check, by its class: only one of the code's loads of the slot goes on to +0xB0, so that
    offset is read from no code.
    """
    holder = await _pointer(reader, bindings_slot, "the slot is empty")
    return await _pointer(reader, holder + HOLDER_BINDINGS, "the holder holds no list")
