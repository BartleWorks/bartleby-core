"""Each client's key bindings, read from its own memory.

The game keeps key bindings with the account, not the install: one install serves every
account, each with its own keys, and the ``LocalInputBindings.xml`` they share holds one
account's at most. So a client's bindings are read from the client, where the game keeps them
as one ``InputBindingList`` whose ``m_inputs`` (0x48) is a list of ``InputBinding``
(r811339's type dump; ``tools/probe_binding_memory.py`` found exactly one list per client, and
a rebind saved in game changed it in place).

**Found once, read on every use.** Finding the list follows a static route from the bindings
holder's slot, about 0.2 ms (``static_routes``), or, when it misses, scans the process, about
four seconds; so its address is kept, per window, and each read goes through it (about 10 ms
for 109 bindings), which sees a rebind the moment the player saves it. An address that no
longer holds the list is found again.

**Background at hook, urgent once needed.** The client manager begins the lookup as a client
hooks, in the background (``priority=BACKGROUND``), so a resize goes first. Every other caller
needs the keys now and asks at urgent, the default: a press, a held key, a run's pre-check. One
asking while a background lookup is under way promotes it, its remaining scans included
(``heap_scan.promote``), and waits on it rather than starting another.

**Never a reason not to run.** A client whose bindings cannot be read gets the game's
defaults for the actions BartleBot presses, with one warning, until ``key_map(refresh=True)``
tries again: a client's attach does. Cannot be read means a lookup that is sure: a stage that
found nothing in a pass that skipped a region the game changed under it scans once more first. A run's start does not, since a build with no list, as
live was at r806919, would scan every client before every run, eight seconds with four.

**Pressed by action.** ``press(client, GameAction.INTERACT)`` sends whatever key that client's
account has Interact on, never a fixed letter; ``test_game_keys_no_fixed_keys`` keeps a fixed
one out. ``docs/KEY_BINDINGS.md``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass

from wizwalker import Keycode
from wizwalker.memory.memory_object import DynamicMemoryObject, PropertyClass

from bartleby.runtime.clients import heap_scan, static_routes
from bartleby.runtime.game.key_bindings import ENCODING, Binding, GameAction, KeyMap, action_label

logger = logging.getLogger(__name__)

# InputBinding's fields (r811339): m_action and m_input are std::strings, m_slot an int.
ACTION, INPUT, SLOT = 0x48, 0x68, 0x94
# InputBindingList.m_inputs: a pointer to the list's sentinel node, then its count. A node is
# next 0x0, prev 0x8, then the InputBinding pointer at 0x10, as wizwalker's
# read_shared_linked_list reads it.
LIST_HEAD, LIST_SIZE = 0x48, 0x50
NODE_NEXT, NODE_VALUE = 0x0, 0x10
MAX_BINDINGS = 1000

# Short enough to sit inside its InputBinding, in a std::string's inline buffer, so a scan for
# it lands on the object. Any action would do; this one is always bound.
_SEED = b"NPCInteract\x00"


@dataclass
class _Entry:
    address: int | None = None
    unreadable: bool = False
    """Memory gave no list: defaults until a refresh, rather than a four-second scan per press."""
    warned: bool = False
    fixed: KeyMap | None = None
    last: KeyMap | None = None
    """The bindings the last read from memory found, for a caller that cannot wait on a read."""
    scan: asyncio.Task | None = None
    """The scan under way, which a second caller waits on rather than starting its own."""
    priority: heap_scan.Priority = heap_scan.Priority.BACKGROUND
    """The scan's, asked again before each of its passes: a caller that needs the keys now raises it."""


_entries: dict[int, _Entry] = {}


class _Object(DynamicMemoryObject, PropertyClass):
    """Any game object at a known address, for its class name."""


def _handle(client) -> int:
    return getattr(client, "window_handle", None) or id(client)


async def type_name(reader, address: int) -> str:
    """The class name of the object at *address*, as the game reflects it ("InputBinding"), or "".

    Many of the addresses asked about are guesses, and a guess fails in many ways.
    """
    if address <= 0x10000:
        return ""
    try:
        return await _Object(reader, address).read_type_name()
    except Exception:  # noqa: BLE001 - see above
        return ""


async def _u64(reader, address: int) -> int:
    return struct.unpack("<Q", await reader.read_bytes(address, 8))[0]


async def _string(reader, address: int) -> str:
    """An MSVC std::string: inline up to 15 bytes, else a pointer; then its size and capacity."""
    raw = await reader.read_bytes(address, 0x20)
    size, capacity = struct.unpack_from("<QQ", raw, 0x10)
    if size > 0x1000:
        raise ValueError(f"a string of {size} bytes")
    data = raw[:size] if capacity < 0x10 else await reader.read_bytes(struct.unpack_from("<Q", raw)[0], size)
    return data.decode(ENCODING)


def _pointers_to(addresses) -> tuple[bytes, ...]:
    """One pattern for any of *addresses* stored as a pointer: a literal for each."""
    return tuple(struct.pack("<Q", a) for a in sorted(set(addresses)))


def _aligned(hits: heap_scan.Hits) -> list[int]:
    """The 8-aligned hits of a one-pattern pass."""
    (found,) = hits
    return [a for a in found if a % 8 == 0]


def _background() -> heap_scan.Priority:
    return heap_scan.Priority.BACKGROUND


async def _holds(reader, value: int, vtable: int) -> bool:
    """Whether a node's value is an object with *vtable*. The sentinel's value is garbage, often unreadable."""
    if value <= 0x10000:
        return False
    try:
        return await _u64(reader, value) == vtable
    except Exception:  # noqa: BLE001 - see above
        return False


async def _ring(reader, start_node: int, vtable: int) -> tuple[int, int] | None:
    """The sentinel and member count of the InputBinding list ring *start_node* is on, or None.

    A member is told by its vtable, one read, rather than by its class name, several: a scan
    turns up a dozen pointers to try, each a ring of a hundred nodes.
    """
    sentinel = None
    members = 0
    node = start_node
    for _ in range(MAX_BINDINGS + 1):
        if not await _holds(reader, await _u64(reader, node + NODE_VALUE), vtable):
            if sentinel is not None:
                return None  # two nodes holding no binding: some other list
            sentinel = node
        else:
            members += 1
        node = await _u64(reader, node + NODE_NEXT)
        if node == start_node:
            return (sentinel, members) if sentinel is not None else None
    return None


async def _seeds(reader, hits: heap_scan.Hits) -> list[int]:
    """The InputBindings holding "NPCInteract" among a pass's hits for it."""
    return [s for s in (hit - ACTION for hit in _aligned(hits)) if await type_name(reader, s) == "InputBinding"]


async def _whole_sets(reader, vtable: int, hits: heap_scan.Hits) -> dict[int, int]:
    """Each ring of two or more bindings the hits' list nodes are on, by sentinel: its member count.

    A ring of one is the bindings page's leftover, no whole set to read.
    """
    rings: dict[int, int] = {}
    for hit in _aligned(hits):
        try:
            found = await _ring(reader, hit - NODE_VALUE, vtable)
        except Exception:  # noqa: BLE001 - a pointer that is no list node leads into garbage
            continue
        if found is not None:
            rings[found[0]] = found[1]
    return {sentinel: members for sentinel, members in rings.items() if members >= 2}


async def _list_at(reader, hits: heap_scan.Hits) -> int | None:
    """The InputBindingList among a pass's hits for pointers to a sentinel, or None."""
    for pointer in _aligned(hits):
        if await type_name(reader, pointer - LIST_HEAD) == "InputBindingList":
            return pointer - LIST_HEAD
    return None


async def binding_list_at(reader, bindings_slot: int) -> int:
    """The account's InputBindingList through the bindings holder's static slot at *bindings_slot*, checked.

    ``[[slot] + 0xB0]``, then the class ``read_binding_list`` checks and a count it can read: a
    whole set, two bindings or more, as the heap's ring must be. Raises ``static_routes.Missed``
    otherwise.
    """
    address = await static_routes.held_binding_list(reader, bindings_slot)
    if await type_name(reader, address) != "InputBindingList":
        raise static_routes.Missed("no InputBindingList at the holder's +0xB0")
    count = struct.unpack("<i", await reader.read_bytes(address + LIST_SIZE, 4))[0]
    if not 2 <= count <= MAX_BINDINGS:
        raise static_routes.Missed("no whole set of bindings in the list")
    return address


async def find_binding_list(reader, priority: Callable[[], heap_scan.Priority] = _background) -> int | None:
    """The address of the client's InputBindingList, or None.

    The static route comes first, from the bindings holder's slot (:func:`binding_list_at`):
    two reads and the list's class, about 0.2 ms. Any miss falls back to the heap
    (``static_routes.follow``), and *priority* is for that.

    There the InputBinding holding "NPCInteract", then the list nodes pointing at it, then the rings
    those nodes are on, then the list pointing at the chosen ring's sentinel from 0x48: three
    scans, each a request to ``heap_scan``, so each may share its pass with whatever else the
    client has waiting, the resizer's names above all. *priority* is asked before each, so a
    lookup promoted partway asks the rest at urgent; it is background unless given. The game's
    key bindings page leaves lists of its own behind, one binding each, inside an
    ``InputBindingData`` per row, and a row can still hold a key the player has since changed;
    so the ring chosen is the one with the most bindings, the player's whole set.

    Each stage is a ``heap_scan.lookup``: a stage that finds nothing in a pass that skipped a
    region the game changed under it scans once more after ``HEAP_SCAN_RETRY_DELAY``, at the
    priority asked for then, and only then is the list not there.
    """
    address = await static_routes.follow(reader, static_routes.BINDINGS, binding_list_at)
    if address is not None:
        return address
    seeds = await heap_scan.lookup(
        reader, _SEED, priority=priority, usable=functools.partial(_seeds, reader), caller="key_map.seed"
    )
    if not seeds:
        return None
    vtable = await _u64(reader, seeds[0])
    rings = await heap_scan.lookup(
        reader,
        _pointers_to(seeds),
        priority=priority,
        usable=functools.partial(_whole_sets, reader, vtable),
        caller="key_map.ring",
    )
    # Largest first, and only a ring a list still points at: a freed list can leave its ring.
    for sentinel in sorted(rings, key=rings.__getitem__, reverse=True):
        address = await heap_scan.lookup(
            reader,
            _pointers_to([sentinel]),
            priority=priority,
            usable=functools.partial(_list_at, reader),
            caller="key_map.list",
        )
        if address is not None:
            return address
    return None


async def read_binding_list(reader, address: int) -> list[Binding]:
    """Every binding in the InputBindingList at *address*. ValueError when it holds none any more."""
    if await type_name(reader, address) != "InputBindingList":
        raise ValueError(f"no InputBindingList at 0x{address:X}")
    sentinel = await _u64(reader, address + LIST_HEAD)
    count = struct.unpack("<i", await reader.read_bytes(address + LIST_SIZE, 4))[0]
    if not 0 < count <= MAX_BINDINGS:
        raise ValueError(f"an InputBindingList of {count} bindings")
    bindings = []
    node = await _u64(reader, sentinel + NODE_NEXT)
    for _ in range(count):
        binding = await _u64(reader, node + NODE_VALUE)
        slot = struct.unpack("<i", await reader.read_bytes(binding + SLOT, 4))[0]
        bindings.append(Binding(await _string(reader, binding + ACTION), await _string(reader, binding + INPUT), slot))
        node = await _u64(reader, node + NODE_NEXT)
    return bindings


async def _find(client, entry: _Entry) -> None:
    """Find the client's list and read it, or mark *entry* unreadable. Never raises."""
    reader = getattr(client, "hook_handler", None)
    try:
        address = await find_binding_list(reader, lambda: entry.priority)
        if address is None:
            raise LookupError("no InputBindingList in the client's memory")
        bindings = await read_binding_list(reader, address)
    except Exception:
        entry.unreadable = True
        logger.debug("Scan for the key bindings of window %s failed", _handle(client), exc_info=True)
        return
    entry.address = address
    entry.last = KeyMap(bindings, source="memory")


async def key_map(
    client,
    *,
    refresh: bool = False,
    warn: bool = True,
    priority: heap_scan.Priority = heap_scan.Priority.URGENT,
) -> KeyMap:
    """The client's key bindings, from its memory; the game's defaults when they cannot be read.

    *refresh* tries memory again for a client that failed before. *warn* False leaves a failure
    for the next caller to warn of: the read made as a client attaches, whose failure the pre-run
    check reports. *priority* is how soon a scan for the list is wanted: urgent, since a caller
    waits, but for that read at attach, which is background. An urgent caller arriving while a
    background scan is under way promotes it, the passes it has still to ask for included.
    Never raises.
    """
    entry = _entries.setdefault(_handle(client), _Entry())
    if entry.fixed is not None:
        return entry.fixed
    if refresh:
        entry.unreadable = entry.warned = False
    if entry.address is not None:
        try:
            entry.last = KeyMap(await read_binding_list(client.hook_handler, entry.address), source="memory")
            return entry.last
        except Exception:  # noqa: BLE001 - moved, or the process is gone; find it again
            entry.address = None
    if not entry.unreadable:
        if entry.scan is None or entry.scan.done():
            entry.priority = priority
            entry.scan = asyncio.create_task(_find(client, entry))
        elif priority > entry.priority:
            # Raised first, so a pass asked for from here on is urgent; then the one waiting.
            entry.priority = priority
            reader = getattr(client, "hook_handler", None)
            if reader is not None:
                heap_scan.promote(reader)
        # Shielded: the caller that began it may be cancelled while another waits on it.
        await asyncio.shield(entry.scan)
        if not entry.unreadable:
            return entry.last
    if warn and not entry.warned:
        entry.warned = True
        logger.warning(
            "Could not read a client's key bindings from its memory (window %s); pressing the game's default keys",
            _handle(client),
        )
    return KeyMap.defaults()


def known_maps() -> list[KeyMap]:
    """The bindings last read from each client's memory, without reading: for the GUI thread.

    Every client read this session, a closed one's included, which suits the one caller, a
    warning about keys the game uses.
    """
    return [entry.last for entry in _entries.values() if entry.last is not None]


def use_map(client, keys: KeyMap | None) -> None:
    """Answer *client*'s bindings with *keys* instead of reading memory; None reads memory again.

    For a client with no game behind it: the tests' FakeClient.
    """
    _entries.setdefault(_handle(client), _Entry()).fixed = keys


class KeyNotPressable(RuntimeError):
    """The client has no key a press of the action can use: bound only with a modifier, say."""


@dataclass(frozen=True)
class VirtualKey:
    """A key code ``Keycode`` has no member for: wizwalker's ``send_key`` reads only ``.value``.

    ``Keycode`` stops short of the punctuation keys (``;`` ``=`` ``[`` and the rest, 0xBA-0xDE),
    which a player can bind like any other.
    """

    value: int

    @property
    def name(self) -> str:
        return f"VK_0x{self.value:02X}"


def keycode(code: int) -> Keycode | VirtualKey:
    """What ``client.send_key`` sends for a virtual-key code."""
    try:
        return Keycode(code)
    except ValueError:
        return VirtualKey(code)


async def press(client, action: GameAction, seconds: float = 0) -> None:
    """Press the key *client*'s account has *action* on, for *seconds* as ``send_key`` does.

    Raises KeyNotPressable, naming the action and why, when it has none a press can use. The
    pre-run check refuses a run before that can happen; this is for a rebind made mid-run.
    """
    choice = (await key_map(client)).choose(action)
    if choice.code is None:
        raise KeyNotPressable(f"{action_label(action)} {choice.problem}")
    await client.send_key(keycode(choice.code), seconds)
