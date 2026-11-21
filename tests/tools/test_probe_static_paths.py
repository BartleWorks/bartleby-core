"""The pure parts of ``tools/probe_static_paths.py``: the pointer and code searches, and the config chain.

The chain is core's (``static_routes``, ``client_resizer.config_nodes_at``), whose own tests are
in ``tests/runtime/clients/``; here the probe's wrapper is followed through a made-up memory map
laid out as the game's is: a slot, the config object, its std::map of sections, the VideoSettings
group and its children inline, each a node whose vtable is in the module.
"""

from __future__ import annotations

import struct
from types import SimpleNamespace

# Above 0x10000, where the chain takes a pointer for a real one.
HEAP = 0x267_0000_0000
SLOT, CONFIG, HEAD, GROUP, CHILDREN = (HEAP + a for a in (0x1000, 0x2000, 0x3000, 0x8000, 0x9000))
NODES = {b"GameSettings": HEAP + 0x4000, b"SoundSettings": HEAP + 0x5000, b"VideoSettings": HEAP + 0x6000}
VECTOR = HEAP + 0x7000
MODULE_BASE, MODULE_SIZE = 0x7FF7_D934_0000, 0x37B_4000
NODE_VTABLE = MODULE_BASE + 0x2CA_0E70


class FakeMemory:
    """Blocks of bytes by address, read as a MemoryReader reads them."""

    def __init__(self) -> None:
        self.blocks: dict[int, bytearray] = {}
        self.process = SimpleNamespace(process_base=SimpleNamespace(lpBaseOfDll=MODULE_BASE, SizeOfImage=MODULE_SIZE))

    def put(self, address: int, data: bytes) -> None:
        block = self.blocks.setdefault(address & ~0xFFF, bytearray(0x1000))
        offset = address & 0xFFF
        block[offset : offset + len(data)] = data

    def qword(self, address: int, value: int) -> None:
        self.put(address, struct.pack("<Q", value))

    async def read_bytes(self, address: int, size: int) -> bytes:
        block = self.blocks[address & ~0xFFF]
        offset = address & 0xFFF
        return bytes(block[offset : offset + size])


def _string(memory: FakeMemory, address: int, text: bytes) -> None:
    memory.put(address, text.ljust(16, b"\x00"))
    memory.put(address + 0x10, struct.pack("<QQ", len(text), 0xF))


def _config(*, marked: bool = True) -> FakeMemory:
    memory = FakeMemory()
    memory.qword(SLOT, CONFIG)
    memory.qword(CONFIG + 0x80, HEAD)
    root, left, right = NODES[b"SoundSettings"], NODES[b"GameSettings"], NODES[b"VideoSettings"]
    memory.put(HEAD, struct.pack("<QQQ", left, root, right) + b"\x01\x01")
    for key, node in NODES.items():
        links = (left, HEAD, right) if node == root else (HEAD, root, HEAD)
        memory.put(node, struct.pack("<QQQ", *links) + b"\x00\x00")
        _string(memory, node + 0x20, key)
    memory.put(right + 0x40, struct.pack("<QQ", VECTOR, VECTOR + 8))
    memory.qword(VECTOR, GROUP)
    memory.put(GROUP + 0x20, struct.pack("<QQ", CHILDREN, CHILDREN + 3 * 0x78))
    for index, (name, mark) in enumerate(((b"UIScale", 0), (b"Resolution", 0), (b"UISize", int(marked)))):
        child = CHILDREN + index * 0x78
        memory.qword(child, NODE_VTABLE)
        memory.qword(child + 0x18, 0xABC0)  # the string a live node points at
        memory.qword(child + 0x38, GROUP)
        memory.put(child + 0x44, bytes([0x00, mark]))
        memory.put(child + 0x50, name + b"\x00")
    return memory


async def test_the_config_chain_reaches_the_live_nodes():
    from tools.probe_static_paths import config_nodes_by_slot

    nodes = await config_nodes_by_slot(_config(), SLOT)

    assert (nodes.ui_size, nodes.resolution) == (CHILDREN + 2 * 0x78, CHILDREN + 0x78)


async def test_the_config_chain_refuses_a_group_with_no_marked_ui_size():
    """The defaults' group has the same names; only the marked UISize is live."""
    from tools.probe_static_paths import config_nodes_by_slot

    assert await config_nodes_by_slot(_config(marked=False), SLOT) is None


def test_pointer_hits_finds_aligned_pointers_into_a_span_only():
    from tools.probe_static_paths import pointer_hits

    target = 0x0000_0267_93B1_F310
    data = bytearray(0x40)
    struct.pack_into("<Q", data, 0x08, target)  # aligned, at the target
    struct.pack_into("<Q", data, 0x10, target - 0x80)  # aligned, inside the span
    struct.pack_into("<Q", data, 0x19, target)  # unaligned
    struct.pack_into("<Q", data, 0x28, target + 0x10)  # past the span

    hits = pointer_hits(bytes(data), 0x5000, [(target - 0x100, target + 8)])

    assert hits == [(0x5008, target), (0x5010, target - 0x80)]


def test_rip_references_and_patterns_resolve_a_rip_relative_operand():
    from tools.probe_static_paths import resolve_pattern, rip_references

    code_rva, slot = 0x1000, 0x33BD0B0
    code = bytearray(b"\xcc" * 0x200)
    # mov rax, [rip+disp] at 0x100: the disp32 at 0x103, the instruction ending at 0x107.
    code[0x100:0x103] = b"\x48\x8b\x05"
    struct.pack_into("<i", code, 0x103, slot - (code_rva + 0x107))

    assert rip_references(bytes(code), code_rva, slot) == [0x103]
    assert resolve_pattern(bytes(code), code_rva, rb"\x48\x8B\x05(....)") == [slot]


def test_fields_read_after_counts_the_member_each_load_goes_on_to():
    from tools.probe_static_paths import fields_read_after

    code = bytearray(b"\xcc" * 0x40)
    code[0x04:0x0B] = b"\x48\x8b\x88\xb0\x00\x00\x00"  # mov rcx, [rax+0xB0]
    code[0x14:0x18] = b"\x48\x83\xee\x80"  # sub rsi, -0x80: rsi + 0x80
    code[0x24:0x28] = b"\x48\x8b\x48\x10"  # mov rcx, [rax+0x10]

    assert fields_read_after(bytes(code), [0x00, 0x10, 0x20]) == {0xB0: 1, 0x80: 1, 0x10: 1}
