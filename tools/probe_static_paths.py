"""Probe: a static path from the game's image to the live config nodes and to the key bindings.

Core found two things in each client by scanning its whole heap, about 1.2 GB and 2.4 s a pass:
the live ``UISize`` and ``Resolution`` config nodes a resize writes
(``client_resizer.find_config_nodes``), and the ``InputBindingList`` holding the account's keys
(``game_keys.find_binding_list``, three passes). With four clients at startup those passes were the
largest cost left. This asks whether a chain of pointers from a static slot in
``WizardGraphicalClient.exe`` reaches each, as wizwalker's patterns reach the video manager and
GameClient, and whether the chain holds on every client. It did, and since 2026-11-20 both finders
follow it first (``bartleby/runtime/clients/static_routes.py``), the heap their fallback; the
patterns, the offsets and the chains' checks here are core's, imported, so this tests what runs.

Read-only. It installs no hook, writes nothing, and presses nothing, so it can run beside a client
being played, and beside BartleBot or BartleBuddy. Start the clients, each in the world.

    uv run --no-sync python tools/probe_static_paths.py              # the chains, on every client
    uv run --no-sync python tools/probe_static_paths.py --watch 10   # read them every 2 s for 10 min
    uv run --no-sync python tools/probe_static_paths.py --search     # rediscover the statics after a patch

The default run reads the build, finds each static by its code pattern, follows each chain, and
checks it against today's heap finders, then climbs from the found nodes to the static to show
each link. ``--watch`` rereads both chains and each client's zone, hook-free, and reports a chain
that moves or breaks and every zone change, for a zone change made by hand while it runs.
``--search`` is the bounded reverse pointer search that found the statics: from today's targets,
level by level, every pointer to a target or into the bytes before it, until a chain ends in the
image; for a build where the patterns no longer match.

Results go to the screen and to ``logs/probe_static_paths/`` (git-ignored). They hold addresses,
offsets and type names, and no wizard, account or zone.

THE CHAINS (r814353)
    The config, from the slot the config object's constructor stores ``this`` in::

        [module + 0x33BD0B0]           the config object, 0xC8 bytes: std::strings ".\\" (+0x00),
                                       "config" (+0x20), then a std::map<std::string, Section>
        +0x80                          the map's head node (MSVC: left 0x0, parent 0x8, right 0x10,
                                       isnil 0x19, key std::string 0x20, value 0x40)
        tree search "VideoSettings"    nine sections, from DebugSettings to VideoSettings
        node + 0x40                    the section: a std::vector<Group*> of one, the user's group
        group + 0x20 .. + 0x28         its children, inline, 0x78 bytes each, count at +0x38
        child + 0x50 == "UISize"       the live UISize; "Resolution" beside it

    The key bindings, from the slot their holder's constructor stores ``this`` in::

        [module + 0x33BD518]           the bindings holder, an object with no reflected type
                                       (vtable module + 0x2CFC1D8)
        +0xB0                          a SharedPointer<InputBindingList>: the account's list
                                       (+0xB8 its reference count block; +0xC8 holds an
                                       ActionList)

    GameClient holds both as well, the config at +0x21390 and the holder at +0x21358.

RESULTS (2026-11-20, the live realm's r814353.Wizard_1_620_0_Live, PE TimeDateStamp 0x6AFBB954;
four clients in the world, each its own process and heap, the image at one base as Windows maps
it once per boot)
    A static chain exists for each, and both held on all four clients.

    ========================  ================================  ===============  ==============
    target                    chain                             by chain         by heap today
    ========================  ================================  ===============  ==============
    live UISize, Resolution   [+0x33BD0B0] +0x80, the tree,     about 12 reads,  one pass,
                              +0x40, [vector] +0x20, by name    0.3 to 0.9 ms    1.8 to 1.9 s
    InputBindingList          [+0x33BD518] +0xB0                2 reads and its  three passes,
                                                                class, 0.2 ms    5.3 to 5.5 s
    ========================  ================================  ===============  ==============

    The heap times are one client at a time on a quiet machine. On every client the config chain
    gave the same two nodes as ``find_config_nodes`` and the bindings chain the same list as
    ``find_binding_list``, with every heap address different from the others'.

    The patterns, each matching once in each client's module and resolving to its slot. The disp32
    after ``48 89`` is the slot; ``.`` is any byte:

    * Config, its constructor's store (``mov byte [rdi+?], 0; mov [rip+slot], rdi; jmp;
      mov rdi, rbx; mov [rsi+?], rdi``): ``C6 87 .... 00 48 89 3D (....) EB . 48 8B FB 48 89 BE ....``
    * Config, its destructor's clear before ``delete`` of 0xC8 bytes:
      ``48 8B 9F .... 48 85 DB 74 . 4C 89 35 (....) 48 8B CB E8 .... BA C8 00 00 00``
    * Bindings holder, its constructor (``mov rdi, rcx; mov [rip+slot], rcx; call; nop;
      lea rax, [vtable]; mov [rdi], rax``): ``48 8B F9 48 89 0D (....) E8 .... 90 48 8D 05 .... 48 89 07``

    The code reads each slot often: 162 RIP-relative references to the config's and 199 to the
    holder's. Right after a load, the config's goes on to +0x80 64 times (``sub r, -0x80`` 53, ``mov
    r, [r+0x80]`` 11), so that offset can be read from code too; the holder's goes to +0xC8 six
    times, +0x30 twice and +0xB0 once, so +0xB0 is better checked by the list's class.

    One update back, on the Test realm's exe on disk (r812087.WizardDev, TimeDateStamp 0x6ADF9072,
    the same old compiler), the three patterns each match once too, at slots +0x33A06D0 and
    +0x33A0B38 (0x468 apart, as on live), and the code after the loads uses +0x80 and +0xB0 the
    same number of times. Not checked: the new compiler (r811339, r813697), whose code differs
    throughout, so each pattern may need a second form as wizwalker's ``AUTOBOT_PATTERNS`` has.

    The climb, from the live UISize, on each client: +0x38 to the VideoSettings group (vtable
    module + 0x2CA0EA0; the nodes' is + 0x2CA0E70), its +0x10 to the section, which is a map
    node's value at +0x40, the node's parent links to the head, the head from the config object's
    +0x80, and that from the slot. The nodes live inline in their group's vector, so nothing
    points at one but the vector's begin at its first: which is why BartleBot's 2026-10-28 search
    back from a node (``tools/probe_runtime_resize.py``) found nothing.

    The holder: the list's pointers on the heap are the holder's +0xB0, the shared pointer's
    count block and the list's own; the holder's one pointer in the image is the slot.

    ``--search --depth 2`` found both slots on all four clients, at level 2, in about 20 s a
    client: the head's and the list's holders, then the slots. Its other image hits were each on
    one client only, pointers that land near the config object by chance, so a static that holds is
    one every client shares.

    ``--watch 6``: 179 rounds on each client, both chains giving their targets on every round,
    and the same addresses as the first run and as a run 25 minutes later; the clients stood still.
    A second ``--watch``, with one client taken through two zone changes by hand (seen at rounds 6
    and 9), had no round where a chain gave nothing, so a loading screen breaks neither.

    A restart: one client closed and started again, a new process and a new heap. Checked at
    character select (``20261120-212141.txt``) and again in the world (``20261120-212232.txt``),
    in the one process: both times both chains held on all four clients and matched the heap
    finders, and the restarted client's targets were the same objects at character select as in
    the world, its UISize, Resolution and list each at one address in both runs. So each slot is
    set by the time character select shows, and holds from there.

    Not tried: a game update, which moves the slots and may move +0x80 and +0xB0. ``--search``
    finds them again, in a few minutes per client.

    Core's finders, the same evening, on the same four clients, read-only as this attaches: the
    route gave what the heap gave on all four. Each client's module scan for its slot, once per
    process, took 84 to 93 ms for the config's and 63 to 68 ms for the holder's (wizwalker's
    ``pattern_scan``, at the pin with no cache across clients); then ``find_config_nodes`` 1.6 to
    2.0 ms, with the resizer's checks of both nodes, and ``find_binding_list`` 0.1 to 0.2 ms,
    against 2.4 to 4.0 s and 6.1 to 6.5 s by the heap.

    Dead ends: the game has no RTTI to name the unreflected classes (the word before a vtable
    leads to no readable complete object locator); ``.data`` holds no pointer to a node, a group,
    a section or the list, only to the two objects above them; and a forward search, every heap
    pointer in ``.data`` (83,146) followed one level and 0x800 bytes, turned up only neighbors of
    the targets in the same small-block heap.
"""

from __future__ import annotations

import argparse
import asyncio
import bisect
import re
import struct
import sys
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pefile
import pymem.exception
import pymem.memory
import pymem.process
from pymem.ressources.structure import MEMORY_PROTECTION, MEMORY_STATE

from bartleby.runtime.clients.client_resizer import (
    NODE_PARENT,
    ConfigNodes,
    config_nodes_at,
    find_config_nodes,
)
from bartleby.runtime.clients.game_keys import binding_list_at, find_binding_list, type_name
from bartleby.runtime.clients.static_routes import (
    BINDINGS,
    CONFIG,
    CONFIG_SECTIONS,
    GROUP_SECTION,
    HOLDER_BINDINGS,
    MAX_TREE_DEPTH,
    MODULE,
    TREE_ISNIL,
    TREE_KEY,
    TREE_PARENT,
    TREE_VALUE,
    Missed,
    std_string,
)
from bartleby.shared.game_install import process_executable

LOG_DIR = Path(__file__).resolve().parents[1] / "logs" / "probe_static_paths"

#: Each static's code patterns, core's (``static_routes``). Here each must match once, and all of a
#: static's must agree, where core takes the first to match once.
PATTERNS: dict[str, tuple[bytes, ...]] = {"config": CONFIG.patterns, "bindings": BINDINGS.patterns}


# The reverse search: how far before a target a pointer may land, and how many targets a level keeps.
SEARCH_BACK = 0x100
SEARCH_CAP = 60
SEARCH_DEPTH = 3
WATCH_INTERVAL = 2.0

_READABLE = frozenset(
    p.value
    for p in (
        MEMORY_PROTECTION.PAGE_EXECUTE_READ,
        MEMORY_PROTECTION.PAGE_EXECUTE_READWRITE,
        MEMORY_PROTECTION.PAGE_READWRITE,
        MEMORY_PROTECTION.PAGE_READONLY,
        MEMORY_PROTECTION.PAGE_WRITECOPY,
    )
)
_TOP = 0x7FFF_FFFF_0000


# -- pure logic ---------------------------------------------------------------------------------


def pointer_hits(data: bytes, base: int, spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Each 8-aligned qword in *data* (at *base*) whose value is in one of *spans*, [lo, hi): (where, value).

    A pointer's top six bytes name the 64 KB page it points into, so each page the spans touch is
    one ``bytes.find`` for those six bytes, and only the hits are unpacked: a whole heap pass
    stays a few seconds, where unpacking every qword in Python would take minutes.
    """
    spans = sorted(spans)
    lows = [lo for lo, _ in spans]
    pages = sorted({page for lo, hi in spans for page in range(lo >> 16, ((hi - 1) >> 16) + 1)})
    found = []
    for page in pages:
        needle = struct.pack("<Q", page << 16)[2:]
        at = data.find(needle, 2)
        while at != -1:
            start = at - 2
            if (base + start) % 8 == 0:
                value = struct.unpack_from("<Q", data, start)[0]
                i = bisect.bisect_right(lows, value) - 1
                while i >= 0:
                    if value < spans[i][1]:
                        found.append((base + start, value))
                        break
                    i -= 1
            at = data.find(needle, at + 1)
    return sorted(found)


def rip_references(code: bytes, code_rva: int, target_rva: int, tails: Iterable[int] = (0, 1, 4)) -> list[int]:
    """The offset in *code* of each disp32 that resolves to *target_rva*, RIP-relative.

    An instruction ends 0, 1 or 4 bytes after its disp32 (no immediate, an imm8, an imm32), and its
    target is that end plus the disp32. Across 64 KB of code the disp32 for one target takes at most
    two values of its top two bytes, so each window is a search for those.
    """
    tails = tuple(tails)
    found = set()
    for window in range(0, len(code), 0x10000):
        stop = min(window + 0x10000, len(code))
        tops = {
            ((target_rva - (code_rva + i + 4 + tail)) & 0xFFFFFFFF) >> 16 for i in (window, stop - 1) for tail in tails
        }
        for top in tops:
            needle = struct.pack("<H", top)
            j = code.find(needle, window + 2, stop + 2)
            while j != -1:
                i = j - 2
                if i + 4 <= len(code):
                    disp = struct.unpack_from("<i", code, i)[0]
                    if any(code_rva + i + 4 + tail + disp == target_rva for tail in tails):
                        found.add(i)
                j = code.find(needle, j + 1, stop + 2)
    return sorted(found)


def fields_read_after(code: bytes, references: Iterable[int]) -> Counter[int]:
    """How often each field offset is used by the instruction right after a load of the slot.

    The slot's loads are ``mov r64, [rip+slot]``, so the next instruction is commonly the object's
    use: ``mov r64, [r64+disp32]``, or ``sub r64, -imm8`` for the address of a member (the
    compiler's spelling of ``add r64, 0x80``). Which offsets the code uses most is what a patch that
    moves a field changes, so this says which constant to trust.
    """
    found: Counter[int] = Counter()
    for field in references:
        following = code[field + 4 : field + 11]
        if len(following) < 4 or following[0] not in (0x48, 0x49, 0x4C, 0x4D):
            continue
        opcode, modrm = following[1], following[2]
        if opcode == 0x8B and modrm >> 6 == 2 and modrm & 7 != 4 and len(following) == 7:
            found[struct.unpack_from("<i", following, 3)[0]] += 1
        elif opcode == 0x8B and modrm >> 6 == 1 and modrm & 7 != 4:
            found[struct.unpack_from("<b", following, 3)[0]] += 1
        elif opcode == 0x83 and modrm >> 6 == 3 and (modrm >> 3) & 7 == 5:
            found[-struct.unpack_from("<b", following, 3)[0]] += 1
    return found


def resolve_pattern(code: bytes, code_rva: int, pattern: bytes) -> list[int]:
    """The RVA each match of *pattern* in *code* points at: its group is a disp32 ending its instruction."""
    found = []
    for match in re.finditer(pattern, code, re.DOTALL):
        end = match.end(1)
        found.append(code_rva + end + struct.unpack_from("<i", code, match.start(1))[0])
    return found


# -- reading the chains ---------------------------------------------------------------------------


async def _u64(reader, address: int) -> int:
    return struct.unpack("<Q", await reader.read_bytes(address, 8))[0]


async def map_head_of(reader, node: int) -> int | None:
    """The head of the tree *node* is in, by its parent links, or None."""
    for _ in range(MAX_TREE_DEPTH):
        raw = await reader.read_bytes(node, TREE_KEY)
        if raw[TREE_ISNIL]:
            return node
        node = struct.unpack_from("<Q", raw, TREE_PARENT)[0]
    return None


async def config_nodes_by_slot(reader, slot: int) -> ConfigNodes | None:
    """The live UISize and Resolution through the config's static *slot*, or None without exactly one pair.

    Core's chain and check (``client_resizer.config_nodes_at``), so the probe tests what the
    resizer runs. A read that fails raises, for ``--watch`` to count.
    """
    try:
        return await config_nodes_at(reader, slot)
    except Missed:
        return None


async def binding_list_by_slot(reader, slot: int) -> int | None:
    """The InputBindingList through the holder's static *slot*, or None when it holds none: core's (``binding_list_at``)."""
    try:
        return await binding_list_at(reader, slot)
    except Missed:
        return None


# -- the module and the heap ----------------------------------------------------------------------


@dataclass
class Module:
    base: int
    size: int
    timestamp: int
    sections: list[tuple[str, int, int, int]]  # name, rva, size, characteristics

    def contains(self, address: int) -> bool:
        return self.base <= address < self.base + self.size

    def section(self, name: str) -> tuple[str, int, int, int]:
        return next(s for s in self.sections if s[0] == name)

    def writable(self) -> list[tuple[str, int, int, int]]:
        return [s for s in self.sections if s[3] & pefile.SECTION_CHARACTERISTICS["IMAGE_SCN_MEM_WRITE"]]


async def read_module(reader) -> Module:
    module = pymem.process.module_from_name(reader.process.process_handle, MODULE)
    pe = pefile.PE(data=await reader.read_bytes(module.lpBaseOfDll, 0x1000), fast_load=True)
    sections = [
        (s.Name.rstrip(b"\x00").decode("ascii", "replace"), s.VirtualAddress, s.Misc_VirtualSize, s.Characteristics)
        for s in pe.sections
    ]
    return Module(module.lpBaseOfDll, module.SizeOfImage, pe.FILE_HEADER.TimeDateStamp, sections)


def regions(handle: int) -> Iterator[tuple[int, bytes]]:
    """Each readable committed region of a process, image included; one the game frees mid-read is skipped."""
    address = 0
    while address < _TOP:
        mbi = pymem.memory.virtual_query(handle, address)
        following = mbi.BaseAddress + mbi.RegionSize
        if mbi.State == MEMORY_STATE.MEM_COMMIT.value and mbi.Protect in _READABLE:
            try:
                yield address, pymem.memory.read_bytes(handle, address, mbi.RegionSize)
            except pymem.exception.WinAPIError:
                pass
        address = following


def revision_of(pid: int) -> str:
    exe = process_executable(pid)
    path = exe.parent / "revision.dat" if exe else None
    return path.read_text(encoding="utf-8").strip() if path and path.exists() else "?"


# -- the runs ---------------------------------------------------------------------------------------


async def statics(reader, module: Module, say) -> dict[str, int | None]:
    """Each static's slot RVA by its patterns, with each pattern's match count."""
    _name, text_rva, text_size, _flags = module.section(".text")
    code = await reader.read_bytes(module.base + text_rva, text_size)
    slots: dict[str, int | None] = {}
    for target, patterns in PATTERNS.items():
        found = set()
        for index, pattern in enumerate(patterns, 1):
            rvas = resolve_pattern(code, text_rva, pattern)
            say(f"    {target} pattern {index}: {len(rvas)} match(es), {', '.join(f'+0x{r:X}' for r in rvas)}")
            if len(rvas) == 1:
                found.add(rvas[0])
        slots[target] = found.pop() if len(found) == 1 else None
        if slots[target] is not None:
            refs = rip_references(code, text_rva, slots[target])
            say(f"    {target} slot +0x{slots[target]:X}, referenced by {len(refs)} instructions")
            used = ", ".join(f"+{offset:#x} {count}x" for offset, count in fields_read_after(code, refs).most_common(4))
            say(f"    {target} fields used right after a load: {used}")
        else:
            say(f"    {target}: NO SINGLE SLOT from its patterns")
    return slots


async def climb(reader, module: Module, nodes: ConfigNodes, config_slot: int | None, say) -> None:
    """From the live UISize to the static, each link."""
    group = await _u64(reader, nodes.ui_size + NODE_PARENT)
    section = await _u64(reader, group + GROUP_SECTION)
    node = section - TREE_VALUE
    head = await map_head_of(reader, node)
    say(f"    UISize {nodes.ui_size:#x} (vtable +0x{await _u64(reader, nodes.ui_size) - module.base:X})")
    say(f"    +0x38 group {group:#x} (vtable +0x{await _u64(reader, group) - module.base:X})")
    say(f"    +0x10 section {section:#x}, key {(await std_string(reader, node + TREE_KEY)).decode('latin-1')!r}")
    say(f"    map node {node:#x}, head {head and hex(head)}")
    if config_slot is not None:
        config = await _u64(reader, module.base + config_slot)
        held = await _u64(reader, config + CONFIG_SECTIONS)
        say(f"    slot +0x{config_slot:X} -> config {config:#x}, +{CONFIG_SECTIONS:#x} -> {held:#x}: {held == head}")


async def holder_of_list(reader, module: Module, bindings_slot: int | None, say) -> None:
    if bindings_slot is None:
        return
    holder = await _u64(reader, module.base + bindings_slot)
    say(
        f"    holder {holder:#x}, vtable +0x{await _u64(reader, holder) - module.base:X}, class {await type_name(reader, holder)!r}"
    )
    for offset in (HOLDER_BINDINGS, HOLDER_BINDINGS + 0x18):
        target = await _u64(reader, holder + offset)
        say(f"      +{offset:#x} -> {target:#x} {await type_name(reader, target)!r}")


async def game_client_fields(client, reader, module: Module, slots: dict[str, int | None], say) -> None:
    """Which of GameClient's fields hold the two objects: a second route, from wizwalker's own static."""
    base = await client.game_client.read_base_address()
    wanted = {}
    for target, slot in slots.items():
        if slot is not None:
            wanted[await _u64(reader, module.base + slot)] = target
    try:
        raw = await reader.read_bytes(base, 0x24000)
    except Exception:  # noqa: BLE001 - an object nearer the end of its region than that
        say("    GameClient: its 0x24000 bytes are not readable")
        return
    for offset in range(0, len(raw), 8):
        value = struct.unpack_from("<Q", raw, offset)[0]
        if value in wanted:
            say(
                f"    GameClient +{offset:#x} -> the {wanted[value]} {'object' if wanted[value] == 'config' else 'holder'}"
            )


def nodes_text(nodes: ConfigNodes | None) -> str:
    return f"UISize {nodes.ui_size:#x}, Resolution {nodes.resolution:#x}" if nodes else "none"


async def check(clients, say) -> int:
    failures = 0
    say(f"Build {revision_of(clients[0].process_id)}")
    for index, client in enumerate(clients, 1):
        reader = client.hook_handler
        module = await read_module(reader)
        say(
            f"\nClient {index}: image at {module.base:#x}, {module.size:#x} bytes, TimeDateStamp 0x{module.timestamp:08X}"
        )
        for name, rva, size, flags in module.sections:
            say(f"    section {name:<8} +{rva:#09X} {size:#09x} {flags:#010x}")
        slots = await statics(reader, module, say)

        started = time.perf_counter()
        by_chain = await config_nodes_by_slot(reader, module.base + slots["config"]) if slots["config"] else None
        chain_s = time.perf_counter() - started
        started = time.perf_counter()
        by_scan = await find_config_nodes(reader)
        scan_s = time.perf_counter() - started
        same = by_chain is not None and by_chain == by_scan
        failures += not same
        say(
            f"  config nodes: chain {nodes_text(by_chain)} in {chain_s * 1000:.1f} ms; "
            f"heap {nodes_text(by_scan)} in {scan_s:.2f} s: {'SAME' if same else 'DIFFERENT'}"
        )

        started = time.perf_counter()
        list_chain = await binding_list_by_slot(reader, module.base + slots["bindings"]) if slots["bindings"] else None
        chain_s = time.perf_counter() - started
        started = time.perf_counter()
        list_scan = await find_binding_list(reader)
        scan_s = time.perf_counter() - started
        same = list_chain is not None and list_chain == list_scan
        failures += not same
        say(
            f"  binding list: chain {list_chain and hex(list_chain)} in {chain_s * 1000:.1f} ms; "
            f"heap {list_scan and hex(list_scan)} in {scan_s:.2f} s: {'SAME' if same else 'DIFFERENT'}"
        )

        if by_scan is not None:
            say("  the climb:")
            await climb(reader, module, by_scan, slots["config"], say)
        say("  the bindings holder:")
        await holder_of_list(reader, module, slots["bindings"], say)
        await game_client_fields(client, reader, module, slots, say)
    say(f"\n{'Every chain held.' if not failures else f'{failures} chain(s) did not match the heap finders.'}")
    return 0 if not failures else 2


async def zone_of(client) -> str | None:
    """The zone the client's objects are in, hook-free: the first of the root object's children with one."""
    try:
        root = await client.game_client.root_client_object()
        for child in (await root.children())[:20]:
            zone = await child.client_zone()
            if zone is not None and (name := await zone.zone_name()):
                return name
    except Exception:  # noqa: BLE001 - mid-load the object tree is half there
        return None
    return None


async def watch(clients, minutes: float, say) -> int:
    """Both chains and the zone, every WATCH_INTERVAL; what moved, and when."""
    state = []
    for client in clients:
        module = await read_module(client.hook_handler)
        slots = await statics(client.hook_handler, module, lambda _line: None)
        state.append({"module": module, "slots": slots, "last": None, "zone": None, "zones": 0, "breaks": 0})
    deadline = time.monotonic() + minutes * 60
    rounds = 0
    while time.monotonic() < deadline:
        rounds += 1
        for index, (client, entry) in enumerate(zip(clients, state, strict=True), 1):
            reader, module, slots = client.hook_handler, entry["module"], entry["slots"]
            try:
                nodes = await config_nodes_by_slot(reader, module.base + slots["config"])
                bindings = await binding_list_by_slot(reader, module.base + slots["bindings"])
            except Exception as exc:  # noqa: BLE001 - a read mid-load; counted, never fatal
                nodes = bindings = None
                say(f"  round {rounds}, client {index}: a read failed: {type(exc).__name__}")
            now = (nodes, bindings)
            zone = await zone_of(client)
            if zone is not None and entry["zone"] is not None and zone != entry["zone"]:
                entry["zones"] += 1
                say(f"  round {rounds}, client {index}: ZONE CHANGED (change {entry['zones']})")
            if zone is not None:
                entry["zone"] = zone
            if None in now:
                entry["breaks"] += 1
                say(f"  round {rounds}, client {index}: a chain gave nothing: {now}")
            elif now != entry["last"]:
                if entry["last"] is not None:
                    say(f"  round {rounds}, client {index}: MOVED from {entry['last']} to {now}")
                entry["last"] = now
        await asyncio.sleep(WATCH_INTERVAL)
    for index, entry in enumerate(state, 1):
        say(
            f"Client {index}: {rounds} rounds, {entry['zones']} zone change(s), {entry['breaks']} round(s) a chain gave nothing"
        )
    return 0


async def search(clients, depth: int, say) -> int:
    """The bounded reverse pointer search, from today's targets to the image."""
    for index, client in enumerate(clients, 1):
        reader = client.hook_handler
        module = await read_module(reader)
        handle = reader.process.process_handle
        nodes = await find_config_nodes(reader)
        level: dict[int, str] = {}
        if nodes is not None:
            group = await _u64(reader, nodes.ui_size + NODE_PARENT)
            level[group] = "group"
            head = await map_head_of(reader, await _u64(reader, group + GROUP_SECTION) - TREE_VALUE)
            if head:
                level[head] = "sections' map head"
        if (bindings := await find_binding_list(reader)) is not None:
            level[bindings] = "InputBindingList"
        say(f"\nClient {index}: from {', '.join(f'{name} {address:#x}' for address, name in level.items())}")
        seen = set(level)
        for step in range(1, depth + 1):
            spans = [(target - SEARCH_BACK, target + 8) for target in level]
            started = time.perf_counter()
            hits = [hit for base, data in regions(handle) for hit in pointer_hits(data, base, spans)]
            say(
                f"  level {step}: {len(level)} target(s), {len(hits)} pointer(s), {time.perf_counter() - started:.1f} s"
            )
            following: dict[int, tuple[str, int]] = {}
            for at, value in hits:
                target = min((t for t in level if t - SEARCH_BACK <= value < t + 8), key=lambda t: abs(t - value))
                chain = f"{level[target]} <- [{at:#x}]{value - target:+#x}"
                if module.contains(at):
                    say(f"    STATIC +0x{at - module.base:X}: {chain}")
                elif at not in seen:
                    seen.add(at)
                    following[at] = (chain, abs(value - target))
            if len(following) > SEARCH_CAP:
                say(f"    {len(following)} heap holders, the {SEARCH_CAP} nearest their targets kept")
            nearest = sorted(following.items(), key=lambda item: item[1][1])[:SEARCH_CAP]
            level = {at: chain for at, (chain, _distance) in nearest}
            if not level:
                break
    return 0


async def main() -> int:
    from wizwalker import ClientHandler  # noqa: PLC0415 - the tests import this module without a game

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--watch", type=float, metavar="MINUTES", help="reread both chains for this long")
    parser.add_argument("--search", action="store_true", help="the reverse pointer search, from today's targets")
    parser.add_argument("--depth", type=int, default=SEARCH_DEPTH, help="the search's levels")
    args = parser.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.txt"
    with path.open("w", encoding="utf-8") as log:

        def say(line: str) -> None:
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()

        clients = ClientHandler().get_new_clients()
        if not clients:
            say("No client is running: start one, in the world, and run this again.")
            return 1
        if args.search:
            code = await search(clients, args.depth, say)
        elif args.watch:
            code = await watch(clients, args.watch, say)
        else:
            code = await check(clients, say)
        say(f"Saved to {path}")
    return code


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(asyncio.run(main()))
