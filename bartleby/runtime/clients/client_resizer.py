"""Changing the size of a running client: the lease, the two config nodes, the apply, and giving back.

``docs/WINDOW_SIZING.md``, "Resizing a running client", has the recipe and what was measured.
wizwalker's ``client.video_manager`` holds the apply, and this holds everything that decides
when one may happen:

**Latest target only.** :meth:`ClientResizer.resize` only says what size a client should be. One
worker per client waits out loading and any lease it can't preempt, and reads the target at
the last moment, so targets sent during a wait run only the last. A fight is not waited out:
the tiles have already changed shape, so a wait would leave a game cut off for the whole fight.

**A lease, below hotkeys.** It writes memory, so it holds ``Owner.DISPLAY``: movement and the
quester are preempted and resume from their own state, and a hotkey, combat or recovery goes
first. Finding the nodes is reads alone and takes seconds, so it happens before the lease, and a
follower walks through it.

**An apply can't be recalled.** The writes and the wait for the byte run as one task of their own
that no cancel reaches, so Stop all can't land between the UI size and the byte, and whoever
embeds the window hears how it ended, as the game has taken it out of its tile by then. Stop all
before that task starts simply ends the resize.

**Any failure means today's behavior.** One warning, the client keeps its last size, and nothing
more is tried on it until :meth:`ClientResizer.forget` and it is hooked again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import struct
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from wizwalker.constants import Primitive
from wizwalker.errors import PatternFailed, WizWalkerMemoryError

from bartleby.runtime.arbiter import Arbiter, Owner, Preempted, Revoked, client_key
from bartleby.runtime.game.game_sizing import ui_size_for
from bartleby.runtime.launcher import launch_profile
from bartleby.runtime.timing import (
    POLL_INTERVAL,
    RESIZE_APPLY_POLL,
    RESIZE_APPLY_TIMEOUT,
    RESIZE_GIVE_BACK_TIMEOUT,
)

logger = logging.getLogger(__name__)

# A config node's fields, from BartleBot's tools/probe_runtime_resize.py, RESULTS (2026-11-01).
NODE_INT, NODE_STRING, NODE_PARENT, NODE_TYPE, NODE_MARK, NODE_NAME = 0x14, 0x18, 0x38, 0x44, 0x45, 0x50
# What +0x44 holds on a node: a string, an int or a float. Anything else is no node.
NODE_TYPES = frozenset({0x00, 0x02, 0x08})

_UI_SIZE = b"UISize"
_RESOLUTION = b"Resolution"

#: Called with a client and the size it measures at, each time an apply has ended, however it did.
Resized = Callable[[object, tuple[int, int]], None]


class _Refused(Exception):
    """An apply did not end at the size asked for."""


@dataclass(frozen=True)
class ConfigNodes:
    """The two live config nodes of one client, by address."""

    ui_size: int
    resolution: int


@dataclass(frozen=True)
class _Node:
    address: int
    marked: bool
    has_string: bool
    parent: int


async def _nodes_called(reader, name: bytes) -> list[_Node]:
    """Every config node called *name*: a scan for the name, then a check of the object around it."""
    module = reader.process.process_base
    try:
        hits = await reader.pattern_scan(re.escape(name) + b"\x00", return_multiple=True)
    except PatternFailed:
        return []
    found = []
    for hit in hits:
        try:
            raw = await reader.read_bytes(hit - NODE_NAME, NODE_NAME)
        except WizWalkerMemoryError:  # the name was only some other string, near the end of a region
            continue
        vtable = struct.unpack_from("<Q", raw)[0]
        in_module = module.lpBaseOfDll <= vtable < module.lpBaseOfDll + module.SizeOfImage
        if in_module and raw[NODE_TYPE] in NODE_TYPES and raw[NODE_MARK] <= 1:
            found.append(
                _Node(
                    hit - NODE_NAME,
                    marked=raw[NODE_MARK] == 1,
                    has_string=struct.unpack_from("<Q", raw, NODE_STRING)[0] != 0,
                    parent=struct.unpack_from("<Q", raw, NODE_PARENT)[0],
                )
            )
    return found


async def find_config_nodes(reader) -> ConfigNodes | None:
    """The client's live UISize and Resolution nodes, or None without exactly one of each.

    Each name has several nodes, and the count moves. The live UISize is the one marked at
    +0x45 with a pointer at +0x18. Its parent is the user's settings, so the live Resolution is
    the one with a pointer under that parent: the other parent holds the defaults, and the
    second class's copies, some of them marked, hold no pointer.
    """
    ui_sizes = [n for n in await _nodes_called(reader, _UI_SIZE) if n.marked and n.has_string]
    if len(ui_sizes) != 1:
        return None
    parent = ui_sizes[0].parent
    resolutions = [n for n in await _nodes_called(reader, _RESOLUTION) if n.has_string and n.parent == parent]
    if len(resolutions) != 1:
        return None
    return ConfigNodes(ui_sizes[0].address, resolutions[0].address)


async def _still_holds(reader, address: int, name: bytes) -> bool:
    try:
        raw = await reader.read_bytes(address, NODE_NAME + len(name) + 1)
    except WizWalkerMemoryError:
        return False
    return raw[NODE_NAME:] == name + b"\x00" and struct.unpack_from("<Q", raw, NODE_STRING)[0] != 0


@dataclass(eq=False)
class _Entry:
    """What the resizer knows of one client."""

    target: tuple[int, int] | None = None
    task: asyncio.Task | None = None
    scan: asyncio.Task | None = None
    nodes: ConfigNodes | None = None
    failed: bool = False
    launch: tuple[int, int] | None = None
    launch_ui_size: int | None = None


class ClientResizer:
    """Each client's size, by lease. One per runtime, used from its event loop only."""

    def __init__(self, arbiter: Arbiter, on_resized: Resized) -> None:
        self._arbiter = arbiter
        self._on_resized = on_resized
        self._entries: dict[int, _Entry] = {}

    def _entry(self, client) -> _Entry:
        return self._entries.setdefault(client_key(client), _Entry())

    # -- asking -------------------------------------------------------------------------------

    def prepare(self, clients: Iterable) -> None:
        """Find what a resize needs ahead of the first swap, once a second client is here.

        With one client this does nothing, so a one-account session never scans. Each client
        scans once, off the event loop, and a failure is that client's one warning.
        """
        clients = list(clients)
        if len(clients) < 2:
            return
        for client in clients:
            entry = self._entry(client)
            scanning = entry.scan is not None and not entry.scan.done()
            if not entry.failed and entry.nodes is None and not scanning:
                entry.scan = asyncio.create_task(self._find(client, entry), name="resize-scan")

    def resize(self, client, size: tuple[int, int]) -> None:
        """Have *client* run at *size*, as soon as it may. Returns at once; ``on_resized`` says how it ended."""
        entry = self._entry(client)
        if entry.failed:
            return
        entry.target = tuple(size)
        if entry.task is None or entry.task.done():
            entry.task = asyncio.create_task(self._run(client, entry, RESIZE_APPLY_TIMEOUT), name="resize")

    async def give_back(self, client) -> None:
        """Put *client* back at its launch size and UI size, on a short timeout, as it leaves its tile.

        A client never resized, or one given up on, has nothing to give back. One still loading
        when the time is up, or held by a higher lease, is left as it is, with a warning.
        """
        entry = self._entries.get(client_key(client))
        if entry is None or entry.failed or entry.launch is None:
            return
        await self._end_waiting(entry)
        entry.target = entry.launch
        entry.task = asyncio.create_task(self._run(client, entry, RESIZE_GIVE_BACK_TIMEOUT), name="resize-back")
        done, _pending = await asyncio.wait({entry.task}, timeout=RESIZE_GIVE_BACK_TIMEOUT)
        if not done:
            await self._end_waiting(entry)
            if not entry.failed:
                logger.warning("Window %s could not go back to its launch size in time", client_key(client))

    async def stop_all(self) -> None:
        """Stop all: every waiting resize ends. An apply under way finishes and reports first."""
        await asyncio.gather(*(self._end_waiting(entry) for entry in self._entries.values()))

    async def forget(self, client) -> None:
        """*client* is unhooked or gone: a client hooked again starts afresh, failures included."""
        entry = self._entries.pop(client_key(client), None)
        if entry is None:
            return
        await self._end_waiting(entry)
        if entry.scan is not None:
            entry.scan.cancel()

    @staticmethod
    async def _end_waiting(entry: _Entry) -> None:
        entry.target = None
        if entry.task is not None:
            entry.task.cancel()
            await asyncio.gather(entry.task, return_exceptions=True)

    # -- finding --------------------------------------------------------------------------------

    async def _find(self, client, entry: _Entry) -> None:
        """Everything a resize reads once, or this client's one warning. Never raises."""
        try:
            video = client.video_manager
            await video.read_base_address()
            await video.pending_flag_offset()
            nodes = await find_config_nodes(client.hook_handler)
            if nodes is None:
                raise _Refused("no single live UISize and Resolution node")
        except Exception as exc:  # noqa: BLE001 - whatever it was, it means leaving this client alone
            self._fail(client, entry, exc)
            return
        entry.nodes = nodes

    async def _nodes(self, client, entry: _Entry) -> ConfigNodes | None:
        """The nodes, checked again since an address can go stale, and found again if it has."""
        nodes = entry.nodes
        if nodes is not None:
            reader = client.hook_handler
            if await _still_holds(reader, nodes.ui_size, _UI_SIZE) and await _still_holds(
                reader, nodes.resolution, _RESOLUTION
            ):
                return nodes
            entry.nodes = None
        if entry.scan is None or entry.scan.done():
            entry.scan = asyncio.create_task(self._find(client, entry), name="resize-scan")
        # Shielded: a Stop all ends this wait, and the scan is worth keeping.
        await asyncio.shield(entry.scan)
        return entry.nodes

    # -- the worker ------------------------------------------------------------------------------

    async def _run(self, client, entry: _Entry, timeout: float) -> None:
        """Bring *client* to its latest target, until there is none."""
        try:
            while entry.target is not None:
                if await self._in_the_way(client):
                    await asyncio.sleep(POLL_INTERVAL)
                elif await self._nodes(client, entry) is not None:
                    await self._under_lease(client, entry, timeout)
        except Exception as exc:  # noqa: BLE001 - a read that failed: the client may be going away
            self._fail(client, entry, exc)

    @staticmethod
    async def _in_the_way(client) -> bool:
        return await client.is_loading()

    async def _under_lease(self, client, entry: _Entry, timeout: float) -> None:
        try:
            async with self._arbiter.acquire(client, Owner.DISPLAY):
                if entry.target is None or await self._in_the_way(client):
                    return
                size, entry.target = entry.target, None
                await self._commit(client, entry, size, timeout)
        except Preempted:
            pass  # a higher owner goes first, and the target stands
        except Revoked:
            entry.target = None

    async def _commit(self, client, entry: _Entry, size: tuple[int, int], timeout: float) -> None:
        """Run the apply out of any cancel's reach, and hold the lease until it has ended."""
        apply = asyncio.create_task(self._apply(client, entry, size, timeout), name="resize-apply")
        try:
            await asyncio.shield(apply)
        except asyncio.CancelledError:
            # Not swallowed: raised again below, once nothing is left under way.
            while not apply.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait({apply})
            raise

    # -- the apply ---------------------------------------------------------------------------------

    async def _apply(self, client, entry: _Entry, size: tuple[int, int], timeout: float) -> None:
        """Change *client* to *size*, and report what it measures. Never raises, and never cancelled."""
        video = client.video_manager
        measured = None
        try:
            measured = await video.read_device_size()
            if measured != size:
                await self._write_and_wait(client, entry, measured, size, timeout)
                logger.info("Window %s now runs at %dx%d", client_key(client), *size)
        except Exception as exc:  # noqa: BLE001 - whatever it was, the client keeps its last size
            self._fail(client, entry, exc)
        if measured is None:
            return
        with contextlib.suppress(Exception):
            measured = await video.read_device_size()
        try:
            self._on_resized(client, measured)
        except Exception:
            logger.exception("The listener to a resize raised")

    async def _write_and_wait(
        self, client, entry: _Entry, was: tuple[int, int], size: tuple[int, int], timeout: float
    ) -> None:
        video, reader, nodes = client.video_manager, client.hook_handler, entry.nodes
        preferred = launch_profile.capture_game_settings()
        ui_was = await reader.read_typed(nodes.ui_size + NODE_INT, Primitive.int32)
        if entry.launch is None:
            entry.launch, entry.launch_ui_size = was, ui_was
        ui_size = entry.launch_ui_size if size == entry.launch else ui_size_for(preferred, size)
        if ui_size is None:
            raise _Refused("the game has no UI scale to size from")

        await reader.write_typed(nodes.ui_size + NODE_INT, ui_size, Primitive.int32)
        try:
            await video.apply_resolution(nodes.resolution, *size)
        except Exception:
            await self._put_back(client, nodes, was, ui_was)
            raise

        cleared = await self._byte_clears(video, timeout)
        if not cleared:
            # Left set, the game would apply it whenever it next could, with nobody watching for
            # a window it takes out of its tile. Set by us, so it is ours to take back.
            await video.write_value_to_offset(await video.pending_flag_offset(), False, Primitive.bool)
        reached = await video.read_device_size()
        if reached != size:
            await self._put_back(client, nodes, was, ui_was)
            if not cleared:
                raise _Refused(f"the pending byte did not clear in {timeout:.0f} s")
            raise _Refused(f"the device is {reached[0]}x{reached[1]}, not {size[0]}x{size[1]}")

    @staticmethod
    async def _byte_clears(video, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while await video.apply_pending():
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(RESIZE_APPLY_POLL)
        return True

    @staticmethod
    async def _put_back(client, nodes: ConfigNodes, size: tuple[int, int], ui_size: int) -> None:
        """The nodes back as they were, so a later apply of the game's own keeps this size. Best effort."""
        reader = client.hook_handler
        with contextlib.suppress(Exception):
            string = await reader.read_typed(nodes.resolution + NODE_STRING, Primitive.int64)
            await client.video_manager.write_string(string, f"{size[0]}x{size[1]}")
            await reader.write_typed(nodes.ui_size + NODE_INT, ui_size, Primitive.int32)

    # -- failing -------------------------------------------------------------------------------------

    @staticmethod
    def _fail(client, entry: _Entry, why: Exception) -> None:
        entry.target = None
        if not entry.failed:
            entry.failed = True
            logger.warning("Resizing is off for window %s until it is hooked again: %s", client_key(client), why)
