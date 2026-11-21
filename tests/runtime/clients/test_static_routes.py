"""Static routes: the code patterns, the slot found once per client, the config's chain, and a miss's reason."""

from __future__ import annotations

import asyncio
import json
import logging
import re

import pytest
from wizwalker.errors import MemoryReadError

from bartleby.runtime import run_trace
from bartleby.runtime.clients import static_routes
from bartleby.runtime.clients.static_routes import BINDINGS, CONFIG, Missed
from bartleby.runtime.testing.sim_client import (
    BINDINGS_STORE,
    CONFIG_CLEAR,
    CONFIG_STORE,
    IMAGE_BASE,
    SimClient,
    rip_operand,
)

_SLOT = 0x7FF7_033B_D0B0
_AT = 0x7FF7_0001_2340

# Each pattern, and code with made-up operands that it was written to match.
_PATTERNS = [
    pytest.param(CONFIG.patterns[0], CONFIG_STORE, id="config-constructor"),
    pytest.param(CONFIG.patterns[1], CONFIG_CLEAR, id="config-destructor"),
    pytest.param(BINDINGS.patterns[0], BINDINGS_STORE, id="bindings-constructor"),
]


@pytest.fixture
def infos(caplog):
    caplog.set_level(logging.INFO, logger=static_routes.logger.name)
    return lambda: [r for r in caplog.records if r.name == static_routes.logger.name]


def _memory():
    return SimClient().hook_handler


# -- the patterns ----------------------------------------------------------------------------------


@pytest.mark.parametrize("pattern, code", _PATTERNS)
def test_each_pattern_matches_its_code_once_and_resolves_to_the_slot(pattern, code):
    placed = rip_operand(code, _AT, _SLOT)
    text = b"\xcc" * 0x40 + placed + b"\xcc" * 0x40

    matches = [m.start() for m in re.finditer(pattern, text, re.DOTALL)]

    assert matches == [0x40]
    assert static_routes.resolve(pattern, placed, _AT) == _SLOT


@pytest.mark.parametrize("pattern, code", _PATTERNS)
def test_no_pattern_matches_the_other_routes_code(pattern, code):
    for other in (CONFIG_STORE, CONFIG_CLEAR, BINDINGS_STORE):
        if other is not code:
            assert static_routes.resolve(pattern, rip_operand(other, _AT, _SLOT), _AT) is None


def test_a_slot_behind_the_code_resolves_by_a_negative_disp32():
    placed = rip_operand(BINDINGS_STORE, _AT, _AT - 0x1000)
    assert static_routes.resolve(BINDINGS.patterns[0], placed, _AT) == _AT - 0x1000


# -- the slot --------------------------------------------------------------------------------------


async def test_the_slot_is_scanned_for_once_per_client():
    memory = _memory()
    memory.place_slot_code()

    assert await static_routes.slot(memory, CONFIG) == memory.config_slot
    assert await static_routes.slot(memory, CONFIG) == memory.config_slot

    assert memory.module_scans == 1
    other = _memory()
    other.place_slot_code()
    assert await static_routes.slot(other, CONFIG) == other.config_slot
    assert other.module_scans == 1, "each client its own"


async def test_no_single_match_is_kept_too_and_never_scanned_for_again():
    memory = _memory()  # code that no pattern matches
    for _ in range(2):
        with pytest.raises(Missed, match="no single pattern match"):
            await static_routes.slot(memory, CONFIG)
    assert memory.module_scans == len(CONFIG.patterns), "each pattern once"


async def test_the_next_pattern_is_tried_when_the_first_does_not_match():
    memory = _memory()
    memory.place_slot_code(CONFIG_CLEAR)
    assert await static_routes.slot(memory, CONFIG) == memory.config_slot
    assert memory.module_scans == 2


async def test_a_pattern_that_matches_twice_gives_no_slot():
    memory = _memory()
    memory.place_slot_code(CONFIG_STORE, IMAGE_BASE)
    memory.place_slot_code(CONFIG_STORE, IMAGE_BASE + 0x100)
    with pytest.raises(Missed, match="no single pattern match"):
        await static_routes.slot(memory, CONFIG)

    memory = _memory()
    memory.place_slot_code(CONFIG_STORE, IMAGE_BASE)
    memory.place_slot_code(CONFIG_STORE, IMAGE_BASE + 0x100)
    memory.place_slot_code(CONFIG_CLEAR, IMAGE_BASE + 0x200)
    assert await static_routes.slot(memory, CONFIG) == memory.config_slot, "the next one matches once"


async def test_a_module_scan_that_raised_is_tried_again():
    memory = _memory()
    memory.place_slot_code()
    real = memory.pattern_scan
    calls = []

    async def pattern_scan(pattern, **kwargs):
        calls.append(pattern)
        if len(calls) == 1:
            raise OSError("the process is busy")
        return await real(pattern, **kwargs)

    memory.pattern_scan = pattern_scan
    with pytest.raises(OSError):
        await static_routes.slot(memory, CONFIG)
    assert await static_routes.slot(memory, CONFIG) == memory.config_slot


# -- following a route -------------------------------------------------------------------------------


async def test_follow_returns_what_the_chain_reaches_from_the_slot(infos):
    memory = _memory()
    memory.place_slot_code()
    reached = []

    async def chain(reader, slot):
        reached.append((reader, slot))
        return "found"

    assert await static_routes.follow(memory, CONFIG, chain) == "found"
    assert reached == [(memory, memory.config_slot)]
    assert infos() == []


async def test_a_chain_s_miss_or_a_failed_read_is_none_logged_once_per_client_naming_why(infos):
    memory = _memory()
    memory.place_slot_code()

    async def missed(reader, slot):
        raise Missed("the holder holds no list")

    async def failed(reader, slot):
        raise MemoryReadError(slot)

    assert await static_routes.follow(memory, CONFIG, missed) is None
    assert await static_routes.follow(memory, CONFIG, failed) is None
    assert [r.levelno for r in infos()] == [logging.INFO]
    assert "the config nodes" in infos()[0].getMessage()
    assert "the holder holds no list" in infos()[0].getMessage()

    assert await static_routes.follow(memory, BINDINGS, missed) is None
    assert await static_routes.follow(_memory(), CONFIG, missed) is None
    assert len(infos()) == 3, "once per client and route"


async def test_a_trace_shows_each_route_s_outcome_and_a_miss_s_reason(tmp_path):
    memory = _memory()
    memory.place_slot_code()

    async def found(reader, slot):
        return 1

    async def failed(reader, slot):
        raise MemoryReadError(slot)

    path = run_trace.start(lambda c: None, tmp_path)
    try:
        await static_routes.follow(memory, CONFIG, found)
        await static_routes.follow(memory, CONFIG, failed)
        await static_routes.follow(memory, BINDINGS, found)
    finally:
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    spans = [(r["name"], r["attrs"]) for r in records if r["kind"] == "span"]
    assert [name for name, _ in spans] == ["config.route", "config.route", "key_map.route"]
    assert spans[0][1]["outcome"] == "route"
    assert "module_scan" in spans[0][1], "the first attempt scanned the module"
    assert spans[1][1] == {"outcome": "heap", "reason": "a read failed (MemoryReadError)"}
    assert spans[2][1]["outcome"] == "heap"
    assert spans[2][1]["reason"] == "no single pattern match", "the holder's code isn't in this module"


async def test_a_cancel_is_never_taken_for_a_miss(infos):
    memory = _memory()
    memory.place_slot_code()

    async def chain(reader, slot):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await static_routes.follow(memory, CONFIG, chain)
    assert infos() == []


async def test_a_reader_that_cannot_scan_misses_and_keeps_nothing(infos):
    class NoModule:
        __slots__ = ()

    async def chain(reader, slot):
        return 1

    assert await static_routes.follow(NoModule(), CONFIG, chain) is None
    assert "AttributeError" in infos()[0].getMessage()


# -- the config's chain ----------------------------------------------------------------------------------


async def test_the_map_is_searched_by_key_and_misses_a_key_it_lacks():
    memory = _memory()
    head = memory.config + static_routes.CONFIG_SECTIONS
    head = int.from_bytes(await memory.read_bytes(head, 8), "little")
    for key, node in memory.sections.items():
        assert await static_routes.map_find(memory, head, key.encode()) == node
    assert await static_routes.map_find(memory, head, b"ZoneSettings") is None
    assert await static_routes.map_find(memory, head, b"AudioSettings") is None


async def test_the_config_s_one_group_holds_the_live_pair():
    memory = _memory()
    (group,) = await static_routes.config_groups(memory, memory.config_slot)
    assert (group.group, group.ui_size, group.resolution) == (
        memory.group,
        memory.ui_size_node,
        memory.resolution_node,
    )


async def test_a_vector_whose_length_is_no_whole_number_of_children_holds_none():
    memory = _memory()
    begin = int.from_bytes(await memory.read_bytes(memory.group + static_routes.GROUP_CHILDREN, 8), "little")
    memory.put_u64(memory.group + static_routes.GROUP_CHILDREN + 8, begin + 0x80)
    assert await static_routes.group_children(memory, memory.group) == {}


def _empty_slot(memory):
    memory.put_u64(memory.config_slot, 0)


def _no_sections(memory):
    memory.put_u64(memory.config + static_routes.CONFIG_SECTIONS, 0)


def _video_settings_renamed(memory):
    memory.put_string(memory.sections["VideoSettings"] + static_routes.TREE_KEY, "VideoSettingz")


def _no_groups(memory):
    memory.put(memory.sections["VideoSettings"] + static_routes.TREE_VALUE, bytes(16))


def _no_names(memory):
    memory.forget_node(memory.ui_size_node)


@pytest.mark.parametrize(
    "breaking, reason",
    [
        (_empty_slot, "the slot is empty"),
        (_no_sections, "the config has no sections"),
        (_video_settings_renamed, "no VideoSettings section"),
        (_no_groups, "no groups in VideoSettings"),
        (_no_names, "no UISize and Resolution in VideoSettings"),
    ],
)
async def test_the_config_s_chain_says_where_it_stopped(breaking, reason):
    memory = _memory()
    breaking(memory)
    with pytest.raises(Missed) as missed:
        await static_routes.config_groups(memory, memory.config_slot)
    assert missed.value.reason == reason
