"""Every scan of a client's whole memory: as few passes as serve them, the urgent first, one at a time.

A heap scan reads every committed region of a client's process, about 1.1 GB, and takes a second
or more. Two features ask for one: the resizer, for its config nodes' names, which a player
waits on, and the key map, for the chain that leads to its ``InputBindingList``, which nothing
waits on until a key is pressed. Each used to ask wizwalker's ``pattern_scan`` on its own, and a
four-client startup ran twenty passes nearly all at once: the first resize landed 17 s after it
was asked for, and the event loop stalled for up to 1.6 s. So every heap scan comes here.

**One pass serves a client's waiting requests.** A request is a client's reader, one or more
patterns, each a literal or a set of literals, and a :class:`Priority`. A pass over a client
takes every request for that client waiting as it starts, reads each region once and searches
it for every pattern, and each requester gets back the hits for its own patterns only, in the
order it asked. Passes start a step of the event loop after a request, so requests made
together share one; a request made while a pass runs waits for the next.

**Urgent first, one at a time.** ``HEAP_SCAN_PASSES`` run at once across every client: one,
since two overlapping passes took as long as the two one after the other, and each requester
waited twice as long. The next pass is for the client whose urgent request has waited longest,
or, with none waiting, the client whose background request has; a background request goes
along with any pass over its client. A new background pass never starts while an urgent
request waits, so an urgent one waits at most for a pass already running. Set above one,
background passes take all but one of the places, so one is kept for urgent work. Background
work is never held off for long, since urgent requests run out: a resize finds its nodes once
per hook.

**A client's background work waits a moment for its resize.** A client with only background
requests waits ``HEAP_SCAN_BACKGROUND_GRACE`` from its first request before a pass of its own,
unless an urgent pass over it comes first, which takes them along. The key map's scan begins at
hook and the GUI asks for a size two or three seconds later, so without the wait the key map
took the only place just before the resize asked.

**Promoted when someone needs it now.** :func:`promote` raises every request waiting on a
client to urgent, as if each had been asked for urgently at that moment, and ends the client's
grace. The key map's lookup asks in the background at hook, and a key pressed before it is done
promotes it, so a follower starting to walk waits a pass, not the queue. A request is raised in
place: it is still one request, served by one pass, beside whatever else waits on its client. A
request whose pass has begun is not waiting, and its pass runs on as it began.

**Searched with bytes.find, never a regex.** wizwalker searches with ``regex``, which lets go of
the GIL and takes it back again and again while it searches for an alternation: beside one busy
thread, three names over 16 MB took 24 s instead of 0.1 s, and the key map's pointer scan is an
alternation. ``bytes.find`` holds the GIL for one call at a time, which for the largest region,
41 MB, is a few tens of milliseconds, and found the same three names in 0.03 s with the thread
busy. The hits are the regex's: leftmost first, the earlier literal at a tie, and never two
overlapping (:func:`find_all`). Hits are few, so finding them one literal at a time costs
little; a multi-literal matcher such as Aho-Corasick would need a new dependency for no gain.

**A region that changes under the read is skipped.** The game frees and changes regions all the
time, most of all as a client settles after a load, and a region freed between asking about it
and reading it fails the read. It is asked about and read once more, then skipped and counted
(:class:`_ProcessRegions`), where wizwalker's walk failed the whole scan: in game, one such
region cost a client its key map for the session. An object in a skipped region is simply not
found, as if it weren't there.

**Cancelled, failed, forgotten.** A requester cancelled while it waits leaves the queue. A pass
under way can't be stopped: it finishes, and serves the rest. A pass that raises, because the
process has exited or its handle is bad, raises to each of its requesters, so each caller's
failure handling holds.
:func:`forget`, as a client is unhooked or its window closes, ends each of its waiting requests
with :class:`ScanDropped`.

**Traced.** Each pass is a ``heap_scan.pass`` span outside any caller's, since it serves several,
with the first requester's slot and ``hwnd`` (the window an enclosing span of the caller names,
for a client not yet on a team), its number (``pass_id``), its ``priority``, how many
``patterns`` and ``requesters``, and once it ends the ``regions``, ``mb``, ``hits`` and
``skipped`` regions. Each request is a ``heap_scan.request`` span inside its caller's, with its
``hwnd``, its ``wait`` in the queue and the ``pass_id`` that served it, so a request's span
less its wait is its pass. A promotion is a ``heap_scan.promoted`` mark, with the client's slot
and ``hwnd``, how many ``requests`` it raised and how long the first had ``waited``, and each
request it raised has ``promoted`` set on its span.
"""

from __future__ import annotations

import asyncio
import contextvars
import enum
import functools
import itertools
import logging
import math
import time
import weakref
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

import pymem.exception
import pymem.memory
from pymem.ressources.structure import MEMORY_PROTECTION, MEMORY_STATE
from wizwalker.errors import ClientClosedError
from wizwalker.memory.memory_reader import MemoryReader
from wizwalker.utils import check_if_process_running

from bartleby.runtime import run_trace
from bartleby.runtime.timing import HEAP_SCAN_BACKGROUND_GRACE, HEAP_SCAN_PASSES

logger = logging.getLogger(__name__)

#: Where wizwalker's walk of a process stops, and so where this one does.
_TOP = 0x7FFF_FFFF_0000
_COMMIT = MEMORY_STATE.MEM_COMMIT.value
#: What a read of a region the game has just freed or changed fails with: ERROR_PARTIAL_COPY,
#: ERROR_INVALID_ADDRESS and ERROR_NOACCESS.
_REGION_CHANGED = frozenset({299, 487, 998})
#: The protections wizwalker reads a region under, exactly: a guard page or no access is skipped.
_READABLE = frozenset(
    p.value
    for p in (
        MEMORY_PROTECTION.PAGE_EXECUTE_READ,
        MEMORY_PROTECTION.PAGE_EXECUTE_READWRITE,
        MEMORY_PROTECTION.PAGE_READWRITE,
        MEMORY_PROTECTION.PAGE_READONLY,
    )
)

#: One region's start address and bytes.
Region = tuple[int, bytes]
#: A literal, or a set of literals any of which is a hit.
Pattern = bytes | Iterable[bytes]


class Priority(enum.IntEnum):
    """How soon a request's pass should run."""

    BACKGROUND = 0
    """Nothing waits on it yet: the key map's warm-up at hook, until a key is pressed (:func:`promote`)."""
    URGENT = 1
    """A player waits on it: a resize."""


class ScanDropped(Exception):
    """The client was unhooked or closed while the request waited, so no pass will serve it."""


def find_all(data: bytes, literals: tuple[bytes, ...]) -> list[int]:
    """Where *literals* are in *data*, as a regex alternation of them finds them.

    Leftmost first, the earlier literal where two start at one place, and never overlapping:
    after a hit the search goes on from its end.
    """
    starts = []
    for rank, literal in enumerate(literals):
        at = data.find(literal)
        while at != -1:
            starts.append((at, rank))
            at = data.find(literal, at + 1)
    starts.sort()
    found, end = [], 0
    for at, rank in starts:
        if at >= end:
            found.append(at)
            end = at + len(literals[rank])
    return found


class _ProcessRegions:
    """Each readable committed region of a live process, lowest first, as wizwalker's walk reads them.

    A region the game frees or changes between asking about it and reading it fails the read
    (``ERROR_PARTIAL_COPY``, mostly, while a client settles after a load). That region is asked
    about again and read once more, since its size or protection may only have changed, then
    skipped and counted in ``skipped``. ``ReadProcessMemory`` says the same of a process that has
    exited, so a failed read is a skip only while the process still runs; anything else, an
    exited process, a bad handle, or a failed ``VirtualQueryEx``, fails the pass.
    """

    def __init__(self, handle: int) -> None:
        self.handle = handle
        self.skipped = 0

    def __iter__(self) -> Iterator[Region]:
        address = 0
        while address < _TOP:
            following, data = self._region(address)
            if data is not None:
                yield address, data
            address = following

    def _region(self, address: int) -> tuple[int, bytes | None]:
        """Where the next region starts, and this one's bytes, or None for one not read."""
        for _attempt in range(2):
            mbi = pymem.memory.virtual_query(self.handle, address)
            following = mbi.BaseAddress + mbi.RegionSize
            if following <= address:
                raise OSError(f"the walk of the process stopped at 0x{address:X}")
            if mbi.State != _COMMIT or mbi.Protect not in _READABLE:
                return following, None
            try:
                return following, pymem.memory.read_bytes(self.handle, address, mbi.RegionSize)
            except pymem.exception.WinAPIError as exc:
                if exc.error_code not in _REGION_CHANGED:
                    raise
                if not check_if_process_running(self.handle):
                    raise ClientClosedError() from exc
        self.skipped += 1
        return following, None


def _regions_of(reader) -> Callable[[], Iterable[Region]]:
    """How to read *reader*'s regions: a test's double answers ``heap_regions()``, a real reader by its process."""
    own = getattr(reader, "heap_regions", None)
    if own is not None:
        return own
    return functools.partial(_ProcessRegions, reader.process.process_handle)


def _walk(
    regions: Callable[[], Iterable[Region]], patterns: list[tuple[bytes, ...]]
) -> tuple[list[list[int]], int, int, int]:
    """One pass, on an executor's thread: each pattern's hits, and how many regions and bytes it read and skipped."""
    hits: list[list[int]] = [[] for _ in patterns]
    count = size = 0
    walk = regions()
    for base, data in walk:
        count += 1
        size += len(data)
        for found, literals in zip(hits, patterns):
            found.extend(base + at for at in find_all(data, literals))
    return hits, count, size, getattr(walk, "skipped", 0)


def _literals(pattern: Pattern) -> tuple[bytes, ...]:
    if isinstance(pattern, bytes | bytearray):
        literals = (bytes(pattern),)
    else:
        literals = tuple(dict.fromkeys(bytes(p) for p in pattern))
    if not literals or not all(literals):
        raise ValueError("a pattern is at least one literal, and no literal is empty")
    return literals


@dataclass(eq=False)
class _Request:
    regions: Callable[[], Iterable[Region]]
    patterns: tuple[tuple[bytes, ...], ...]
    priority: Priority
    future: asyncio.Future
    order: int
    slot: int | None
    hwnd: int | None
    queued: float = field(default_factory=time.perf_counter)
    began: float | None = None
    pass_id: int | None = None
    promoted: bool = False


class HeapScanner:
    """The queue of one event loop's heap scans. Use the module's :func:`scan` and :func:`forget`."""

    def __init__(
        self, passes: int | None = None, background_passes: int | None = None, grace: float | None = None
    ) -> None:
        self.passes = HEAP_SCAN_PASSES if passes is None else passes
        # With one place, background work takes it when nothing urgent waits; with more, all but one.
        self.background_passes = max(1, self.passes - 1) if background_passes is None else background_passes
        self.grace = HEAP_SCAN_BACKGROUND_GRACE if grace is None else grace
        self._waiting: dict[object, list[_Request]] = {}
        # When each client first asked (the loop's clock), and which have had an urgent pass.
        self._first_asked: dict[object, float] = {}
        self._had_urgent: set[object] = set()
        self._wake: asyncio.TimerHandle | None = None
        self._running = 0
        self._running_background = 0
        self._order = itertools.count()
        self._pass_ids = itertools.count(1)
        self._tasks: set[asyncio.Task] = set()
        self._dispatching = False

    @property
    def running(self) -> int:
        """Passes under way."""
        return self._running

    def waiting(self, reader) -> int:
        """Requests waiting on *reader*'s next pass."""
        return len(self._waiting.get(reader, ()))

    async def scan(self, reader, patterns: Iterable[Pattern], priority: Priority) -> list[list[int]]:
        """Each pattern's hits in *reader*'s memory, from the next pass over it. See the module."""
        regions = _regions_of(reader)
        literals = tuple(_literals(p) for p in patterns)
        if not literals:
            raise ValueError("a scan is for at least one pattern")
        loop = asyncio.get_running_loop()
        request = _Request(
            regions,
            literals,
            Priority(priority),
            loop.create_future(),
            next(self._order),
            run_trace.current_slot(),
            run_trace.enclosing("hwnd"),
        )
        where = {} if request.hwnd is None else {"hwnd": request.hwnd}
        with run_trace.span(
            "heap_scan.request", priority=request.priority.name.lower(), patterns=len(literals), **where
        ):
            self._first_asked.setdefault(reader, loop.time())
            self._waiting.setdefault(reader, []).append(request)
            self._dispatch()
            try:
                return await request.future
            finally:
                self._withdraw(reader, request)
                if request.promoted:
                    run_trace.note(promoted=True)
                if request.began is not None:
                    run_trace.note(wait=round(request.began - request.queued, 4), pass_id=request.pass_id)

    def forget(self, reader) -> None:
        """End each request waiting on *reader* with :class:`ScanDropped`. A pass under way runs on.

        Hooked again, the client starts afresh, its background grace included.
        """
        self._first_asked.pop(reader, None)
        self._had_urgent.discard(reader)
        for request in self._waiting.pop(reader, []):
            if not request.future.done():
                request.future.set_exception(ScanDropped("the client is gone or unhooked"))

    def promote(self, reader) -> int:
        """Raise each background request waiting on *reader* to urgent, and end its grace. How many it raised.

        Each is queued as if asked for urgently now, so an urgent request already waiting goes
        first. A pass under way is left as it is.
        """
        self._had_urgent.add(reader)
        raised = [r for r in self._waiting.get(reader, ()) if r.priority is not Priority.URGENT]
        for request in raised:
            request.priority, request.promoted, request.order = Priority.URGENT, True, next(self._order)
        if raised:
            first = raised[0]
            slot = first.slot if first.slot is not None else run_trace.current_slot()
            hwnd = first.hwnd if first.hwnd is not None else run_trace.enclosing("hwnd")
            run_trace.mark(
                "heap_scan.promoted",
                slot=slot,
                requests=len(raised),
                waited=round(time.perf_counter() - first.queued, 4),
                **({} if hwnd is None else {"hwnd": hwnd}),
            )
            self._dispatch()
        return len(raised)

    def _withdraw(self, reader, request: _Request) -> None:
        waiting = self._waiting.get(reader)
        if waiting is not None and request in waiting:
            waiting.remove(request)
            if not waiting:
                del self._waiting[reader]

    def _next(self) -> object | None:
        """The reader the next pass is over, or None while none may start.

        Each reader's requests are in the order they came, so its first is its longest waiting.
        A client with background requests alone waits out its grace first, unless it has had an
        urgent pass, and the loop is asked to look again when the earliest grace ends.
        """
        urgent = [
            (r.order, reader) for reader, reqs in self._waiting.items() for r in reqs if r.priority is Priority.URGENT
        ]
        if urgent:
            return min(urgent, key=lambda pair: pair[0])[1]
        if self._running_background >= self.background_passes or not self._waiting:
            return None
        now = asyncio.get_running_loop().time()
        ready = [(reqs[0].order, reader) for reader, reqs in self._waiting.items() if self._ready_at(reader) <= now]
        if ready:
            return min(ready, key=lambda pair: pair[0])[1]
        self._wake_at(min(self._ready_at(reader) for reader in self._waiting))
        return None

    def _ready_at(self, reader) -> float:
        """When *reader*'s background requests may have a pass of their own (the loop's clock)."""
        if reader in self._had_urgent:
            return -math.inf
        return self._first_asked.get(reader, -math.inf) + self.grace

    def _wake_at(self, when: float) -> None:
        if self._wake is not None:
            if self._wake.when() <= when:
                return
            self._wake.cancel()
        self._wake = asyncio.get_running_loop().call_at(when, self._woken)

    def _woken(self) -> None:
        self._wake = None
        self._dispatch()

    def _dispatch(self) -> None:
        """Start what may start, once the loop's other ready tasks have run.

        So requests made together, by tasks started in one step (a ``gather``, or a client's
        hook beside its first resize), are waiting when the pass begins, and share it.
        """
        if not self._dispatching:
            self._dispatching = True
            asyncio.get_running_loop().call_soon(self._start_passes)

    def _start_passes(self) -> None:
        self._dispatching = False
        while self._running < self.passes:
            reader = self._next()
            if reader is None:
                return
            requests = self._waiting.pop(reader)
            priority = max(r.priority for r in requests)
            self._running += 1
            if priority is Priority.BACKGROUND:
                self._running_background += 1
            else:
                self._had_urgent.add(reader)
            # Outside every requester's context: a pass serves several, so it nests under none.
            task = asyncio.get_running_loop().create_task(
                self._pass(requests, priority, next(self._pass_ids)), name="heap-scan", context=contextvars.Context()
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _pass(self, requests: list[_Request], priority: Priority, pass_id: int) -> None:
        began = time.perf_counter()
        for request in requests:
            request.began, request.pass_id = began, pass_id
        patterns = list(dict.fromkeys(p for r in requests for p in r.patterns))
        slot = next((r.slot for r in requests if r.slot is not None), None)
        hwnd = next((r.hwnd for r in requests if r.hwnd is not None), None)
        try:
            with run_trace.span(
                "heap_scan.pass",
                slot=slot,
                pass_id=pass_id,
                priority=priority.name.lower(),
                patterns=len(patterns),
                requesters=len(requests),
                **({} if hwnd is None else {"hwnd": hwnd}),
            ):
                hits, regions, size, skipped = await MemoryReader.run_in_executor(_walk, requests[0].regions, patterns)
                run_trace.note(regions=regions, mb=round(size / 2**20, 1), hits=sum(map(len, hits)), skipped=skipped)
            if skipped:
                logger.debug(
                    "Heap pass %d skipped %d region(s) that changed as it read them (window %s)", pass_id, skipped, hwnd
                )
        except asyncio.CancelledError:
            # The loop is closing: no further pass starts.
            self._finish(priority)
            for request in requests:
                request.future.cancel()
            raise
        except Exception as exc:  # noqa: BLE001 - each requester's own scan would have raised it
            for request in requests:
                if not request.future.done():
                    request.future.set_exception(exc)
        else:
            by_pattern = dict(zip(patterns, hits))
            for request in requests:
                if not request.future.done():
                    request.future.set_result([list(by_pattern[p]) for p in request.patterns])
        self._finish(priority)
        self._dispatch()

    def _finish(self, priority: Priority) -> None:
        self._running -= 1
        if priority is Priority.BACKGROUND:
            self._running_background -= 1


# One per event loop: a scanner's futures and tasks belong to the loop that made them.
_scanners: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, HeapScanner] = weakref.WeakKeyDictionary()


def scanner() -> HeapScanner:
    """The running event loop's scanner, made on first use."""
    loop = asyncio.get_running_loop()
    found = _scanners.get(loop)
    if found is None:
        found = _scanners[loop] = HeapScanner()
    return found


async def scan(reader, *patterns: Pattern, priority: Priority) -> list[list[int]]:
    """Each of *patterns*' hits in the memory *reader* reads, in order, from one pass shared with whatever else waits.

    A pattern is a literal or a set of literals. Raises what the pass raised, or
    :class:`ScanDropped` if :func:`forget` was told of the client first. No hit is an empty list.
    """
    return await scanner().scan(reader, patterns, priority)


def promote(reader) -> int:
    """Someone needs *reader*'s waiting background work now: it is raised to urgent. How many requests it raised.

    See :meth:`HeapScanner.promote`.
    """
    return scanner().promote(reader)


def forget(reader) -> None:
    """*reader*'s client is unhooked or gone: its waiting requests end with :class:`ScanDropped`."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    found = _scanners.get(loop)
    if found is not None:
        found.forget(reader)
