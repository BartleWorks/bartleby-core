"""The heap scanner, on made-up memory: shared passes, their order and limit, cancels, failures, the trace."""

from __future__ import annotations

import asyncio
import json
import re
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from bartleby.runtime import run_trace
from bartleby.runtime.clients import game_keys, heap_scan
from bartleby.runtime.clients.client_resizer import find_config_nodes
from bartleby.runtime.clients.heap_scan import HeapScanner, Priority, ScanDropped, find_all
from bartleby.runtime.testing.sim_client import SimClient

BASE = 0x1000_0000
URGENT, BACKGROUND = Priority.URGENT, Priority.BACKGROUND


class Memory:
    """A process of a few regions. Its passes are logged in *log*, and wait for ``gate`` to open."""

    def __init__(self, name: str, log: list[str], *regions: bytes) -> None:
        self.name = name
        self.log = log
        self.regions = [(BASE + 0x10000 * i, data) for i, data in enumerate(regions or [b""])]
        self.scans = 0
        self.gate = threading.Event()
        self.gate.set()
        self.error: Exception | None = None

    def heap_regions(self):
        self.scans += 1
        self.log.append(self.name)
        assert self.gate.wait(5), "a test left a pass waiting"
        if self.error is not None:
            raise self.error
        return self.regions

    def hold(self) -> None:
        self.gate.clear()


@pytest.fixture(autouse=True)
def no_grace(monkeypatch):
    """Background work starts at once, but in the tests of the grace, which give their own."""
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_BACKGROUND_GRACE", 0.0)


@pytest.fixture
def log() -> list[str]:
    return []


@pytest.fixture
def memories(log):
    """Make a Memory; every gate is opened at the end, so no executor thread is left waiting."""
    made: list[Memory] = []

    def make(name: str, *regions: bytes) -> Memory:
        memory = Memory(name, log, *regions)
        made.append(memory)
        return memory

    yield make
    for memory in made:
        memory.gate.set()


async def _until(condition, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "it never happened"
        await asyncio.sleep(0.002)


def _start(scanner: HeapScanner, memory: Memory, *patterns, priority: Priority = URGENT) -> asyncio.Task:
    return asyncio.create_task(scanner.scan(memory, patterns, priority))


# -- one pass for a client's waiting requests ---------------------------------------------------


async def test_two_requests_waiting_for_one_client_are_one_pass_each_with_its_own_hits(memories):
    scanner = HeapScanner(passes=1, background_passes=1)
    busy = memories("busy")
    busy.hold()
    first = _start(scanner, busy, b"x")
    await _until(lambda: busy.scans == 1)

    memory = memories("client", b"..UISize\0..Resolution\0..NPCInteract\0..")
    resize = _start(scanner, memory, b"UISize\0", b"Resolution\0")
    keys = _start(scanner, memory, b"NPCInteract\0", priority=BACKGROUND)
    await _until(lambda: scanner.waiting(memory) == 2)
    busy.gate.set()

    assert await resize == [[BASE + 2], [BASE + 11]]
    assert await keys == [[BASE + 24]]
    assert memory.scans == 1
    assert await first == [[]]


async def test_requests_made_together_share_a_pass_with_nothing_else_running(memories):
    memory = memories("client", b"ab")
    scanner = HeapScanner()
    assert await asyncio.gather(scanner.scan(memory, [b"a"], BACKGROUND), scanner.scan(memory, [b"b"], URGENT)) == [
        [[BASE]],
        [[BASE + 1]],
    ]
    assert memory.scans == 1


async def test_a_request_made_during_a_pass_waits_for_the_next(memories):
    scanner = HeapScanner(passes=2, background_passes=1)
    memory = memories("client", b"ab")
    memory.hold()
    first = _start(scanner, memory, b"a")
    await _until(lambda: memory.scans == 1)
    second = _start(scanner, memory, b"b")
    await asyncio.sleep(0.01)
    memory.gate.set()
    assert await first == [[BASE]]
    assert await second == [[BASE + 1]]
    assert memory.scans == 2


async def test_the_resizer_s_two_names_are_one_pass():
    memory = SimClient().hook_handler
    assert await find_config_nodes(memory) is not None
    assert memory.scans == 1


async def test_a_hit_never_spans_two_regions(memories):
    memory = memories("client", b"..UI", b"Size\0..UISize\0")
    assert await HeapScanner().scan(memory, [b"UISize\0"], URGENT) == [[BASE + 0x10000 + 7]]


# -- the same hits as wizwalker's regex ----------------------------------------------------------


@pytest.mark.parametrize(
    "data, literals",
    [
        (b"aaaa", (b"aa",)),  # overlapping copies of one literal: the regex takes every other
        (b"abcabc", (b"bc", b"abc")),  # leftmost wins over the earlier literal
        (b"abcd", (b"ab", b"abc")),  # at one place, the earlier literal
        (b"abcd", (b"abc", b"ab")),
        (b"xyzyzyx", (b"yzy", b"zyx")),  # a hit hides one that overlaps it
        (b"\0\0\0\0\0\0\0\0\0\0", (b"\0\0\0",)),
        (b"nothing here", (b"absent",)),
    ],
)
def test_find_all_finds_what_an_alternation_does(data, literals):
    pattern = b"|".join(re.escape(literal) for literal in literals)
    assert find_all(data, literals) == [m.start() for m in re.finditer(pattern, data, re.DOTALL)]


async def test_the_key_map_s_patterns_find_what_they_found_before(memories):
    """The seed literal, and pointers to the seeds as the alternation game_keys built, unaligned ones included."""
    seeds = [0x2000_0110, 0x2000_0230, 0x2000_0350]
    data = bytearray(0x200)
    data[0x10:0x1C] = b"NPCInteract\0"
    for at, seed in ((0x40, seeds[0]), (0x83, seeds[1]), (0x88, seeds[2]), (0x100, seeds[0])):
        data[at : at + 8] = struct.pack("<Q", seed)
    memory = memories("client", bytes(data))
    old = b"|".join(re.escape(struct.pack("<Q", a)) for a in sorted(set(seeds)))

    found = await HeapScanner().scan(memory, [b"NPCInteract\0", game_keys._pointers_to(seeds)], BACKGROUND)

    assert found[0] == [BASE + m.start() for m in re.finditer(re.escape(b"NPCInteract\0"), bytes(data), re.DOTALL)]
    assert found[1] == [BASE + m.start() for m in re.finditer(old, bytes(data), re.DOTALL)]


# -- order and limit --------------------------------------------------------------------------------


async def test_an_urgent_request_waiting_with_a_background_one_is_served_first(memories, log):
    scanner = HeapScanner(passes=1, background_passes=1)
    busy = memories("busy")
    busy.hold()
    first = _start(scanner, busy, b"x")
    await _until(lambda: busy.scans == 1)
    keys = _start(scanner, memories("keys"), b"x", priority=BACKGROUND)
    resize = _start(scanner, memories("resize"), b"x")
    await asyncio.sleep(0)
    busy.gate.set()
    await asyncio.gather(first, keys, resize)
    assert log == ["busy", "resize", "keys"]


async def test_the_limit_holds_across_clients(memories):
    scanner = HeapScanner(passes=2, background_passes=2)
    clients = [memories(f"client {i}") for i in range(4)]
    for memory in clients:
        memory.hold()
    tasks = [_start(scanner, memory, b"x") for memory in clients]
    await _until(lambda: sum(m.scans for m in clients) == 2)
    await asyncio.sleep(0.02)
    assert scanner.running == 2
    assert sum(m.scans for m in clients) == 2
    for memory in clients:
        memory.gate.set()
    await asyncio.gather(*tasks)
    assert [m.scans for m in clients] == [1, 1, 1, 1]
    assert scanner.running == 0


async def test_a_place_is_kept_for_urgent_work(memories):
    """Background passes never fill every place, so a resize asked for starts at once."""
    scanner = HeapScanner(passes=2, background_passes=1)
    first, second, urgent = memories("first"), memories("second"), memories("urgent")
    first.hold()
    keys = [_start(scanner, first, b"x", priority=BACKGROUND), _start(scanner, second, b"x", priority=BACKGROUND)]
    await _until(lambda: first.scans == 1)
    await asyncio.sleep(0.01)
    assert second.scans == 0, "a second background pass took the urgent place"
    assert await asyncio.wait_for(scanner.scan(urgent, [b"x"], URGENT), 5) == [[]]
    first.gate.set()
    await asyncio.gather(*keys)
    assert second.scans == 1


async def test_background_requests_are_served_once_the_urgent_ones_are(memories, log):
    scanner = HeapScanner(passes=2, background_passes=1)
    background = [_start(scanner, memories(f"keys {i}"), b"x", priority=BACKGROUND) for i in range(3)]
    urgent = [_start(scanner, memories(f"resize {i}"), b"x") for i in range(3)]
    await asyncio.wait_for(asyncio.gather(*background, *urgent), 5)
    assert sorted(log) == sorted([f"keys {i}" for i in range(3)] + [f"resize {i}" for i in range(3)])


async def test_a_background_request_goes_along_with_an_urgent_pass_over_its_client(memories):
    scanner = HeapScanner(passes=2, background_passes=1)
    busy, memory = memories("busy"), memories("client", b"keys resize")
    busy.hold()
    first = _start(scanner, busy, b"x", priority=BACKGROUND)
    await _until(lambda: busy.scans == 1)
    keys = _start(scanner, memory, b"keys", priority=BACKGROUND)
    await asyncio.sleep(0.01)
    assert memory.scans == 0, "the background place is taken"
    assert await scanner.scan(memory, [b"resize"], URGENT) == [[BASE + 5]]
    assert await keys == [[BASE]]
    assert memory.scans == 1
    busy.gate.set()
    await first


# -- cancelling, failing, forgetting ------------------------------------------------------------------


async def test_a_cancelled_waiting_request_leaves_the_queue_and_the_rest_are_served(memories):
    scanner = HeapScanner(passes=1, background_passes=1)
    busy, memory = memories("busy"), memories("client", b"ab")
    busy.hold()
    first = _start(scanner, busy, b"x")
    await _until(lambda: busy.scans == 1)
    gone, stays = _start(scanner, memory, b"a"), _start(scanner, memory, b"b")
    await _until(lambda: scanner.waiting(memory) == 2)
    gone.cancel()
    await asyncio.gather(gone, return_exceptions=True)
    assert scanner.waiting(memory) == 1
    busy.gate.set()
    assert await stays == [[BASE + 1]]
    await first


async def test_a_cancel_during_a_pass_leaves_the_pass_for_its_other_requesters(memories):
    scanner = HeapScanner()
    memory = memories("client", b"ab")
    memory.hold()
    gone, stays = _start(scanner, memory, b"a"), _start(scanner, memory, b"b")
    await _until(lambda: memory.scans == 1)
    gone.cancel()
    await asyncio.gather(gone, return_exceptions=True)
    memory.gate.set()
    assert await stays == [[BASE + 1]]
    assert gone.cancelled()
    assert memory.scans == 1


async def test_a_failing_pass_raises_to_every_requester(memories):
    scanner = HeapScanner()
    memory = memories("client")
    memory.hold()
    memory.error = OSError("the process is gone")
    tasks = [_start(scanner, memory, b"a"), _start(scanner, memory, b"b", priority=BACKGROUND)]
    await _until(lambda: memory.scans == 1)
    memory.gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    assert [type(r) for r in results] == [OSError, OSError]
    assert await scanner.scan(memories("next", b"a"), [b"a"], URGENT) == [[BASE]], "a failed pass frees its place"


async def test_a_reader_with_no_memory_raises_before_it_waits():
    with pytest.raises(AttributeError):
        await HeapScanner().scan(None, [b"a"], URGENT)


async def test_a_forgotten_client_s_waiting_requests_are_dropped(memories):
    busy, memory = memories("busy"), memories("client", b"a")
    busy.hold()
    first = asyncio.create_task(heap_scan.scan(busy, b"x", priority=URGENT))
    await _until(lambda: busy.scans == 1)
    waiting = [
        asyncio.create_task(heap_scan.scan(memory, b"a", priority=URGENT)),
        asyncio.create_task(heap_scan.scan(memory, b"a", priority=BACKGROUND)),
    ]
    await _until(lambda: heap_scan.scanner().waiting(memory) == 2)
    heap_scan.forget(memory)
    results = await asyncio.gather(*waiting, return_exceptions=True)
    assert [type(r) for r in results] == [ScanDropped, ScanDropped]
    assert memory.scans == 0
    busy.gate.set()
    await first


def test_forgetting_with_no_loop_running_is_nothing():
    heap_scan.forget(object())


def test_a_pattern_needs_a_literal():
    with pytest.raises(ValueError):
        heap_scan._literals(())
    with pytest.raises(ValueError):
        heap_scan._literals(b"")


# -- the walk of a real process ---------------------------------------------------------------------------


_COMMIT = heap_scan.MEMORY_STATE.MEM_COMMIT.value
_READWRITE = heap_scan.MEMORY_PROTECTION.PAGE_READWRITE.value
_PARTIAL_COPY, _NOACCESS, _INVALID_HANDLE = 299, 998, 6


class Process:
    """pymem's two calls over a made-up address space, whose regions can fail to read or change."""

    def __init__(self, monkeypatch, regions: dict[int, tuple[int, int, int]]) -> None:
        self.regions = regions
        self.failures: dict[int, list[int]] = {}  # address: the error codes its next reads fail with
        self.running = True
        self.reads: list[int] = []
        monkeypatch.setattr(heap_scan.pymem.memory, "virtual_query", self.virtual_query)
        monkeypatch.setattr(heap_scan.pymem.memory, "read_bytes", self.read_bytes)
        monkeypatch.setattr(heap_scan, "check_if_process_running", lambda handle: self.running)

    def virtual_query(self, handle, address):
        size, state, protect = self.regions[address]
        return SimpleNamespace(BaseAddress=address, RegionSize=size, State=state, Protect=protect)

    def read_bytes(self, handle, address, size):
        self.reads.append(address)
        failing = self.failures.get(address)
        if failing:
            raise heap_scan.pymem.exception.WinAPIError(failing.pop(0))
        return f"<{address:x}>".encode().ljust(min(size, 0x10), b".")  # the last region runs to the top


def _three_regions() -> dict[int, tuple[int, int, int]]:
    return {
        0x0: (0x10, _COMMIT, _READWRITE),
        0x10: (0x10, _COMMIT, _READWRITE),
        0x20: (heap_scan._TOP - 0x20, _COMMIT, _READWRITE),
    }


class Reader:
    """A real reader's shape, a process handle, so a pass takes heap_scan's own walk."""

    process = SimpleNamespace(process_handle=7)


def test_the_walk_reads_only_committed_readable_regions(monkeypatch):
    reserve = heap_scan.MEMORY_STATE.MEM_RESERVE.value
    guarded = _READWRITE | heap_scan.MEMORY_PROTECTION.PAGE_GUARD.value
    Process(
        monkeypatch,
        {
            0x0: (0x10000, reserve, _READWRITE),
            0x10000: (0x10, _COMMIT, _READWRITE),
            0x10010: (0x10, _COMMIT, guarded),
            0x10020: (heap_scan._TOP - 0x10020, _COMMIT, heap_scan.MEMORY_PROTECTION.PAGE_READONLY.value),
        },
    )
    assert [address for address, _ in heap_scan._ProcessRegions(7)] == [0x10000, 0x10020]


def test_a_region_that_changes_under_the_read_is_skipped_and_counted(monkeypatch):
    process = Process(monkeypatch, _three_regions())
    process.failures[0x10] = [_PARTIAL_COPY, _NOACCESS]
    walk = heap_scan._ProcessRegions(7)
    assert [address for address, _ in walk] == [0x0, 0x20]
    assert walk.skipped == 1
    assert process.reads.count(0x10) == 2, "read once more before it is skipped"


def test_a_region_asked_about_again_after_a_failed_read_is_read_at_its_new_size(monkeypatch):
    process = Process(monkeypatch, _three_regions())
    process.failures[0x10] = [_PARTIAL_COPY]
    first_answer = process.virtual_query

    def virtual_query(handle, address):
        if address == 0x10 and 0x10 in process.reads:
            return SimpleNamespace(BaseAddress=0x10, RegionSize=0x8, State=_COMMIT, Protect=_READWRITE)
        return first_answer(handle, address)

    monkeypatch.setattr(heap_scan.pymem.memory, "virtual_query", virtual_query)
    process.regions[0x18] = (0x8, _COMMIT, _READWRITE)
    walk = heap_scan._ProcessRegions(7)
    assert [(address, len(data)) for address, data in walk][:3] == [(0x0, 0x10), (0x10, 0x8), (0x18, 0x8)]
    assert walk.skipped == 0


def test_a_failed_read_of_a_process_that_has_exited_fails_the_walk(monkeypatch):
    process = Process(monkeypatch, _three_regions())
    process.failures[0x10] = [_PARTIAL_COPY]
    process.running = False
    with pytest.raises(heap_scan.ClientClosedError):
        list(heap_scan._ProcessRegions(7))


def test_a_bad_handle_fails_the_walk(monkeypatch):
    process = Process(monkeypatch, _three_regions())
    process.failures[0x0] = [_INVALID_HANDLE]
    with pytest.raises(heap_scan.pymem.exception.WinAPIError):
        list(heap_scan._ProcessRegions(7))


async def test_a_pass_with_a_skipped_region_still_returns_the_hits_elsewhere(monkeypatch, caplog):
    process = Process(monkeypatch, _three_regions())
    process.failures[0x10] = [_PARTIAL_COPY, _PARTIAL_COPY]
    caplog.set_level("DEBUG", logger=heap_scan.logger.name)
    assert await HeapScanner().scan(Reader(), [b"<0>", b"<10>", b"<20>"], URGENT) == [[0x0], [], [0x20]]
    assert len([r for r in caplog.records if "skipped 1 region" in r.getMessage()]) == 1


async def test_a_pass_over_a_process_that_has_exited_fails_every_requester(monkeypatch):
    process = Process(monkeypatch, _three_regions())
    process.failures[0x10] = [_PARTIAL_COPY]
    process.running = False
    scanner, reader = HeapScanner(), Reader()
    results = await asyncio.gather(
        scanner.scan(reader, [b"<0>"], URGENT), scanner.scan(reader, [b"<20>"], BACKGROUND), return_exceptions=True
    )
    assert [type(r) for r in results] == [heap_scan.ClientClosedError, heap_scan.ClientClosedError]


# -- one place, and the grace ----------------------------------------------------------------------------


def test_background_passes_take_the_one_place_or_all_but_one():
    assert HeapScanner().passes == 1
    assert HeapScanner(passes=1).background_passes == 1
    assert HeapScanner(passes=2).background_passes == 1
    assert HeapScanner(passes=4).background_passes == 3


async def test_one_pass_runs_at_a_time_by_default(memories):
    scanner = HeapScanner()
    clients = [memories(f"client {i}") for i in range(3)]
    for memory in clients:
        memory.hold()
    tasks = [_start(scanner, memory, b"x") for memory in clients]
    await _until(lambda: sum(m.scans for m in clients) == 1)
    await asyncio.sleep(0.02)
    assert (scanner.running, sum(m.scans for m in clients)) == (1, 1)
    for memory in clients:
        memory.gate.set()
    await asyncio.gather(*tasks)
    assert [m.scans for m in clients] == [1, 1, 1]


async def test_an_urgent_request_behind_a_running_background_pass_is_next(memories, log):
    scanner = HeapScanner()
    running = memories("running")
    running.hold()
    first = _start(scanner, running, b"x", priority=BACKGROUND)
    await _until(lambda: running.scans == 1)
    keys = _start(scanner, memories("keys"), b"x", priority=BACKGROUND)
    resize = _start(scanner, memories("resize"), b"x")
    await asyncio.sleep(0.01)
    running.gate.set()
    await asyncio.gather(first, keys, resize)
    assert log == ["running", "resize", "keys"]


async def test_a_client_s_background_work_waits_out_its_grace_once(memories):
    scanner = HeapScanner(grace=0.1)
    memory = memories("client", b"keys")
    began = time.monotonic()
    assert await scanner.scan(memory, [b"keys"], BACKGROUND) == [[BASE]]
    assert time.monotonic() - began >= 0.09
    began = time.monotonic()
    await scanner.scan(memory, [b"keys"], BACKGROUND)
    assert time.monotonic() - began < 0.09, "the grace runs from a client's first request"


async def test_an_urgent_pass_takes_a_client_s_background_work_along_before_its_grace_ends(memories):
    scanner = HeapScanner(grace=5)
    memory = memories("client", b"keys resize")
    keys = _start(scanner, memory, b"keys", priority=BACKGROUND)
    await asyncio.sleep(0.02)
    assert memory.scans == 0, "the place is left for the resize"
    assert await asyncio.wait_for(scanner.scan(memory, [b"resize"], URGENT), 2) == [[BASE + 5]]
    assert await keys == [[BASE]]
    assert memory.scans == 1
    # After its urgent pass, a client's background work waits no longer.
    assert await asyncio.wait_for(scanner.scan(memory, [b"keys"], BACKGROUND), 2) == [[BASE]]


async def test_the_grace_holds_only_the_client_it_belongs_to(memories, log):
    scanner = HeapScanner(grace=5)
    fresh = _start(scanner, memories("fresh"), b"x", priority=BACKGROUND)
    await asyncio.sleep(0)
    old = memories("old", b"x")
    scanner._first_asked[old] = asyncio.get_running_loop().time() - 10
    assert await asyncio.wait_for(scanner.scan(old, [b"x"], BACKGROUND), 2) == [[BASE]]
    assert log == ["old"]
    fresh.cancel()
    await asyncio.gather(fresh, return_exceptions=True)


async def test_forgetting_a_client_starts_its_grace_afresh(memories):
    scanner = HeapScanner(grace=5)
    memory = memories("client", b"x")
    await scanner.scan(memory, [b"x"], URGENT)
    scanner.forget(memory)
    keys = _start(scanner, memory, b"x", priority=BACKGROUND)
    await asyncio.sleep(0.02)
    assert memory.scans == 1, "hooked again, it waits for its resize again"
    keys.cancel()
    await asyncio.gather(keys, return_exceptions=True)


# -- promotion ---------------------------------------------------------------------------------------------


async def test_a_promoted_request_is_served_at_once_its_grace_ended(memories):
    scanner = HeapScanner(grace=5)
    memory = memories("client", b"keys")
    keys = _start(scanner, memory, b"keys", priority=BACKGROUND)
    await _until(lambda: scanner.waiting(memory) == 1)
    await asyncio.sleep(0.02)
    assert memory.scans == 0, "waiting out its grace"
    assert scanner.promote(memory) == 1
    assert await asyncio.wait_for(keys, 2) == [[BASE]]
    assert memory.scans == 1
    # Its grace is over: the client's background work waits no longer.
    assert await asyncio.wait_for(scanner.scan(memory, [b"keys"], BACKGROUND), 2) == [[BASE]]


async def test_a_promoted_request_is_one_request_and_shares_its_pass(memories, log):
    """Raised in place: one pass serves it and the client's other waiting work, behind an urgent request asked first."""
    scanner = HeapScanner(passes=1, background_passes=1)
    busy, memory = memories("busy"), memories("client", b"keys more")
    busy.hold()
    first = _start(scanner, busy, b"x", priority=BACKGROUND)
    await _until(lambda: busy.scans == 1)
    keys = _start(scanner, memory, b"keys", priority=BACKGROUND)
    more = _start(scanner, memory, b"more", priority=BACKGROUND)
    resize = _start(scanner, memories("resize"), b"x")
    await _until(lambda: scanner.waiting(memory) == 2)
    assert scanner.promote(memory) == 2
    assert scanner.waiting(memory) == 2, "raised in place, not asked again"
    busy.gate.set()
    assert await asyncio.gather(first, keys, more, resize) == [[[]], [[BASE]], [[BASE + 5]], [[]]]
    assert log == ["busy", "resize", "client"], "the resize was urgent first"
    assert memory.scans == 1


async def test_promoting_a_client_whose_pass_has_begun_changes_nothing(memories):
    scanner = HeapScanner()
    memory = memories("client", b"keys")
    memory.hold()
    keys = _start(scanner, memory, b"keys", priority=BACKGROUND)
    await _until(lambda: memory.scans == 1)
    assert scanner.promote(memory) == 0
    assert scanner.running == 1
    memory.gate.set()
    assert await keys == [[BASE]]
    assert memory.scans == 1
    assert (scanner.running, scanner._running_background) == (0, 0), "the background pass gave its place back"
    assert await asyncio.wait_for(scanner.scan(memory, [b"keys"], BACKGROUND), 2) == [[BASE]]


async def test_an_urgent_request_is_not_promoted(memories):
    scanner = HeapScanner(passes=1, background_passes=1)
    busy, memory = memories("busy"), memories("client", b"x")
    busy.hold()
    first = _start(scanner, busy, b"x")
    await _until(lambda: busy.scans == 1)
    resize = _start(scanner, memory, b"x")
    await _until(lambda: scanner.waiting(memory) == 1)
    assert scanner.promote(memory) == 0
    assert scanner.promote(memories("nothing waiting")) == 0
    busy.gate.set()
    await asyncio.gather(first, resize)


async def test_the_module_promotes_on_the_running_loop_s_scanner(memories, monkeypatch):
    monkeypatch.setattr(heap_scan, "HEAP_SCAN_BACKGROUND_GRACE", 5.0)
    memory = memories("client", b"keys")
    keys = asyncio.create_task(heap_scan.scan(memory, b"keys", priority=BACKGROUND))
    await _until(lambda: heap_scan.scanner().waiting(memory) == 1)
    assert heap_scan.promote(memory) == 1
    assert await asyncio.wait_for(keys, 2) == [[BASE]]


# -- the trace ---------------------------------------------------------------------------------------------


async def test_a_trace_shows_each_pass_and_each_request_s_wait(memories, tmp_path):
    scanner = HeapScanner(passes=1, background_passes=1)
    busy, memory = memories("busy"), memories("client", b"ab")
    path = run_trace.start(lambda client: None, tmp_path)
    try:
        busy.hold()
        with run_trace.span("caller", slot=2):
            first = _start(scanner, busy, b"x")
        await _until(lambda: busy.scans == 1)
        with run_trace.span("resize", slot=3, hwnd=77), run_trace.span("resize.config_nodes"):
            resize = _start(scanner, memory, b"a", b"b")
        with run_trace.span("keys", slot=3, hwnd=77):
            keys = _start(scanner, memory, b"a", priority=BACKGROUND)
        await asyncio.sleep(0.02)
        busy.gate.set()
        await asyncio.gather(first, resize, keys)
    finally:
        run_trace.stop()

    spans = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    spans = [r for r in spans if r["kind"] == "span"]
    by_id = {r["id"]: r for r in spans}
    passes = [r for r in spans if r["name"] == "heap_scan.pass"]
    assert [(p["slot"], p["parent"]) for p in passes] == [(2, None), (3, None)]
    assert "hwnd" not in passes[0]["attrs"]
    shared = passes[1]["attrs"]
    assert shared["priority"] == "urgent"
    assert (shared["patterns"], shared["requesters"], shared["hits"], shared["skipped"]) == (2, 2, 2, 0)
    assert shared["hwnd"] == 77, "the window an enclosing span names, for a client on no team yet"
    requests = [r for r in spans if r["name"] == "heap_scan.request"]
    assert sorted(by_id[r["parent"]]["name"] for r in requests) == ["caller", "keys", "resize.config_nodes"]
    for request in requests:
        if by_id[request["parent"]]["name"] != "caller":
            assert request["attrs"]["pass_id"] == shared["pass_id"]
            assert request["attrs"]["wait"] >= 0.02
            assert request["attrs"]["hwnd"] == 77


async def test_a_trace_counts_the_regions_a_pass_skipped(monkeypatch, tmp_path):
    process = Process(monkeypatch, _three_regions())
    process.failures[0x10] = [_PARTIAL_COPY, _PARTIAL_COPY]
    path = run_trace.start(lambda client: None, tmp_path)
    try:
        await HeapScanner().scan(Reader(), [b"<0>"], URGENT)
    finally:
        run_trace.stop()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    (pass_,) = [r for r in records if r.get("name") == "heap_scan.pass"]
    assert (pass_["attrs"]["regions"], pass_["attrs"]["skipped"]) == (2, 1)


async def test_a_trace_shows_a_promotion(memories, tmp_path):
    scanner = HeapScanner(grace=5)
    memory = memories("client", b"keys")
    path = run_trace.start(lambda client: None, tmp_path)
    try:
        with run_trace.span("client.key_map", hwnd=77):
            keys = _start(scanner, memory, b"keys", priority=BACKGROUND)
        await _until(lambda: scanner.waiting(memory) == 1)
        await asyncio.sleep(0.02)
        with run_trace.span("press", slot=2):
            scanner.promote(memory)
        await asyncio.wait_for(keys, 2)
    finally:
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    (mark,) = [r for r in records if r["kind"] == "mark" and r["name"] == "heap_scan.promoted"]
    assert (mark["slot"], mark["hwnd"], mark["requests"]) == (2, 77, 1), "the promoter's slot, the requester's window"
    assert mark["waited"] >= 0.02
    (request,) = [r for r in records if r.get("name") == "heap_scan.request"]
    assert (request["attrs"]["priority"], request["attrs"]["promoted"]) == ("background", True), "asked as, and raised"
    (pass_,) = [r for r in records if r.get("name") == "heap_scan.pass"]
    assert pass_["attrs"]["priority"] == "urgent"
