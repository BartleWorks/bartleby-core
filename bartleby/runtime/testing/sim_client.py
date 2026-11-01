"""A simulated client: a real client's interface, with no game behind it.

The arbiter, following, spacing and the quester are where most bugs will be, and none of
them should need the game to test. A :class:`SimClient` answers the reads they make
(position, yaw, zone, loading, battle, the speed field, the character, the interact prompt), takes the
input they send (keys pressed and held, yaw and speed written), logs every input with the
time it came, and walks while its forward key is held, at the speed its speed field says.
Its ``hook_handler`` is the few bytes a resize finds its config nodes in, laid out as the
game's, and its ``video_manager`` a fake apply of the size those nodes say.

Movement is integrated on demand against a clock the test can drive (:class:`SimClock`),
so a test runs as fast as it likes and lands where the arithmetic says. Walking speed is
600 units a second on foot, as measured (BartleBot's docs/WALKING.md), times the speed
field's bonus.
"""

from __future__ import annotations

import asyncio
import itertools
import math
import re
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace

from wizwalker import XYZ
from wizwalker.errors import MemoryReadError, PatternFailed

from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction, KeyMap

#: Units a second on foot, on flat ground.
WALK_SPEED = 600.0

_handles = itertools.count(50_000)

# Where a simulated client's config nodes are: in a made-up module and heap, as the game's.
_MODULE_BASE, _MODULE_SIZE = 0x7FF7_0000_0000, 0x400_0000
_HEAP_BASE, _HEAP_SIZE = 0x2000_0000, 0x4000
#: The vtables of a config node, and of the second class's copies.
NODE_VTABLE, COPY_VTABLE = _MODULE_BASE + 0x1000, _MODULE_BASE + 0x2000
#: The parents a config node can have: the user's settings, and the defaults.
USER_SETTINGS, DEFAULT_SETTINGS = 0x1111_0000, 0x2222_0000
_NODE_INT, _NODE_STRING, _NODE_PARENT, _NODE_TYPE, _NODE_MARK, _NODE_NAME = 0x14, 0x18, 0x38, 0x44, 0x45, 0x50
_STRING_TYPE, _INT_TYPE = 0x00, 0x02
_PENDING_OFFSET = 0x2229B


class SimClock:
    """A clock a test moves by hand. ``SimClock()()`` is the time now."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class InputRecord:
    time: float
    kind: str  # "press", "down", "up", "yaw", "speed", "ui_size", "resolution", "pending"
    detail: object


@dataclass
class _Body:
    sim: SimClient

    async def position(self) -> XYZ:
        self.sim.advance()
        return XYZ(*self.sim.position)

    async def yaw(self) -> float:
        return self.sim.yaw

    async def write_yaw(self, yaw: float) -> None:
        self.sim.advance()
        self.sim.yaw = yaw % math.tau
        self.sim.log("yaw", yaw)


@dataclass
class _ClientObject:
    sim: SimClient

    async def maybe_read_type_name(self) -> str:
        return self.sim.type_name

    async def read_value_from_offset(self, offset: int, primitive) -> int:
        return self.sim.character

    async def speed_multiplier(self) -> int:
        return self.sim.speed

    async def write_speed_multiplier(self, value: int) -> None:
        self.sim.advance()
        self.sim.speed = value
        self.sim.log("speed", value)


@dataclass
class _Keys:
    sim: SimClient

    async def down(self, code: int) -> None:
        self.sim.advance()
        if code not in self.sim.held:
            self.sim.held.add(code)
            self.sim.log("down", code)

    async def up(self, code: int) -> None:
        self.sim.advance()
        if code in self.sim.held:
            self.sim.held.discard(code)
            self.sim.log("up", code)


class SimMemory:
    """The part of a client's memory a resize touches, read and scanned as the game's would be.

    Holds the two live config nodes and the defaults' two, as every session has them, and
    ``place_node`` stages any other shape. ``scan_seconds`` is how long a scan takes, spent on
    an executor's thread as wizwalker's is.
    """

    def __init__(self, sim: SimClient) -> None:
        self.sim = sim
        self.data = bytearray(_HEAP_SIZE)
        self.next = 0x100
        self.scans = 0
        self.scan_seconds = 0.0
        self.process = SimpleNamespace(process_base=SimpleNamespace(lpBaseOfDll=_MODULE_BASE, SizeOfImage=_MODULE_SIZE))
        width, height = sim.game_size
        self.ui_size_node = self.place_node("UISize", marked=True, text=str(sim.ui_size), value=sim.ui_size)
        self.resolution_node = self.place_node("Resolution", text=f"{width}x{height}", kind=_STRING_TYPE)
        self.place_node("UISize", parent=DEFAULT_SETTINGS, text="2", value=2)
        self.place_node("Resolution", parent=DEFAULT_SETTINGS, text="autodetect", kind=_STRING_TYPE)

    def alloc(self, size: int) -> int:
        address = _HEAP_BASE + self.next
        self.next += (size + 0xF) & ~0xF
        return address

    def place_node(
        self,
        name: str,
        *,
        vtable: int = NODE_VTABLE,
        parent: int = USER_SETTINGS,
        kind: int = _INT_TYPE,
        marked: bool = False,
        value: int = 0,
        text: str | None = None,
    ) -> int:
        """Put a config node in memory, as the game lays one out. No *text* is no string pointer."""
        address = self.alloc(_NODE_NAME + 0x20)
        string = 0
        if text is not None:
            string = self.alloc(0x20)
            self.put_string(string, text)
        self._put(address, struct.pack("<Q", vtable))
        self._put(address + _NODE_INT, struct.pack("<i", value))
        self._put(address + _NODE_STRING, struct.pack("<Q", string))
        self._put(address + _NODE_PARENT, struct.pack("<Q", parent))
        self._put(address + _NODE_TYPE, bytes([kind, 1 if marked else 0]))
        self._put(address + _NODE_NAME, name.encode() + b"\x00")
        return address

    def place_text(self, text: str) -> int:
        """Put a bare string in memory, which a scan for a node's name also finds."""
        address = self.alloc(_NODE_NAME + len(text) + 1)
        self._put(address + _NODE_NAME, text.encode() + b"\x00")
        return address

    def forget_node(self, address: int) -> None:
        """Wipe a node, as the game does when it frees one."""
        self._put(address, bytes(_NODE_NAME + 0x20))

    def resolution_text(self) -> str:
        """The live Resolution node's string, as the game would read it."""
        string = struct.unpack("<Q", self._get(self.resolution_node + _NODE_STRING, 8))[0]
        size = struct.unpack("<Q", self._get(string + 0x10, 8))[0]
        return self._get(string, size).decode()

    def put_string(self, address: int, text: str) -> None:
        """An MSVC std::string, inline: 16 bytes, then its size and its capacity."""
        raw = text.encode()
        self._put(address, raw.ljust(16, b"\x00") + struct.pack("<QQ", len(raw), 15))

    def _put(self, address: int, raw: bytes) -> None:
        self.data[address - _HEAP_BASE : address - _HEAP_BASE + len(raw)] = raw

    def _get(self, address: int, size: int) -> bytes:
        if not _HEAP_BASE <= address <= _HEAP_BASE + _HEAP_SIZE - size:
            raise MemoryReadError(address)
        return bytes(self.data[address - _HEAP_BASE : address - _HEAP_BASE + size])

    async def pattern_scan(self, pattern: bytes, *, module=None, return_multiple: bool = False):
        self.scans += 1
        if self.scan_seconds:
            await asyncio.get_running_loop().run_in_executor(None, time.sleep, self.scan_seconds)
        found = [_HEAP_BASE + m.start() for m in re.finditer(pattern, bytes(self.data), re.DOTALL)]
        if not found:
            raise PatternFailed(pattern)
        return found if return_multiple else found[0]

    async def read_bytes(self, address: int, size: int) -> bytes:
        return self._get(address, size)

    async def write_bytes(self, address: int, value: bytes) -> None:
        self._put(address, value)
        if address == self.ui_size_node + _NODE_INT:
            self.sim.ui_size = struct.unpack("<i", value)[0]
            self.sim.log("ui_size", self.sim.ui_size)

    async def read_typed(self, address: int, data_type):
        return data_type.value.unpack(self._get(address, data_type.value.size))[0]

    async def write_typed(self, address: int, value, data_type) -> None:
        await self.write_bytes(address, data_type.value.pack(value))


class SimVideoManager:
    """A fake of wizwalker's ``VideoManager``: the byte clears, and the device takes the node's size.

    ``apply_seconds`` is how long an apply takes. ``hangs`` leaves the byte set, as a device that
    can't reset does. ``error`` is raised by every check, as a pattern or an offset that fails
    is, before anything is written. ``lands_at`` is a size the apply goes to instead of the
    node's, which breaks the 1:1 contract.
    """

    def __init__(self, sim: SimClient) -> None:
        self.sim = sim
        self.pending = False
        self.apply_seconds = 0.0
        self.hangs = False
        self.error: Exception | None = None
        self.lands_at: tuple[int, int] | None = None
        self._since = 0.0

    def _check(self) -> None:
        if self.error is not None:
            raise self.error

    async def read_base_address(self) -> int:
        self._check()
        return _MODULE_BASE

    async def pending_flag_offset(self) -> int:
        self._check()
        return _PENDING_OFFSET

    async def read_device_size(self) -> tuple[int, int]:
        self._check()
        return self.sim.game_size

    async def write_string(self, address: int, string: str) -> None:
        self.sim.hook_handler.put_string(address, string)
        self.sim.log("resolution", string)

    async def apply_resolution(self, resolution_node: int, width: int, height: int) -> None:
        self._check()
        memory = self.sim.hook_handler
        string = struct.unpack("<Q", await memory.read_bytes(resolution_node + _NODE_STRING, 8))[0]
        await self.write_string(string, f"{width}x{height}")
        self.pending = True
        self._since = self.sim.clock()
        self.sim.log("pending", True)

    async def apply_pending(self) -> bool:
        if self.pending and not self.hangs and self.sim.clock() - self._since >= self.apply_seconds:
            self.pending = False
            width, height = self.sim.hook_handler.resolution_text().split("x")
            self.sim.game_size = self.lands_at or (int(width), int(height))
        return self.pending

    async def write_value_to_offset(self, offset: int, value, data_type) -> None:
        assert offset == _PENDING_OFFSET
        self.pending = bool(value)
        self.sim.log("pending", self.pending)


@dataclass(eq=False)
class SimClient:
    """One simulated game client. Set its fields to stage a scene; read ``inputs`` after."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    yaw: float = 0.0
    zone: str | None = "WizardCity/WC_Hub"
    loading: bool = False
    battle: bool = False
    speed: int = 0
    #: The character id and type name its client object reads as; ``log_out`` stages a freed one.
    character: int = 1
    type_name: str = "WizClientObject"
    npc_range: bool = False
    dialog: bool = False
    #: The size its render device and window are at, and its live UI size.
    game_size: tuple[int, int] = (800, 600)
    ui_size: int = 2
    clock: Callable[[], float] = time.monotonic
    window_handle: int = field(default_factory=lambda: next(_handles))
    inputs: list[InputRecord] = field(default_factory=list)
    held: set[int] = field(default_factory=set)
    #: Called with the key's action whenever a key is pressed (not held): a test's hook to
    #: make the press do something, the way Interact takes a wizard through a door.
    on_press: Callable[[GameAction | int], None] | None = None

    def __post_init__(self) -> None:
        self.body = _Body(self)
        self.client_object = _ClientObject(self)
        self.key_sender = _Keys(self)
        self.hook_handler = SimMemory(self)
        self.video_manager = SimVideoManager(self)
        self._last = self.clock()
        self._keys = KeyMap.defaults()
        game_keys.use_map(self, self._keys)
        self._actions = {self._keys.choose(a).code: a for a in GameAction}
        self.hooked = True

    # -- the scene ------------------------------------------------------------------

    def log(self, kind: str, detail: object) -> None:
        self.inputs.append(InputRecord(self.clock(), kind, detail))

    @property
    def walk_speed(self) -> float:
        return WALK_SPEED * (1 + self.speed / 100)

    def holding(self, action: GameAction) -> bool:
        return self._keys.choose(action).code in self.held

    def advance(self) -> None:
        """Walk the wizard for the time since the last call, with the keys held meanwhile."""
        now = self.clock()
        dt, self._last = max(0.0, now - self._last), now
        if dt == 0 or self.loading or self.battle:
            return
        forward = self.holding(GameAction.MOVE_FORWARD) - self.holding(GameAction.MOVE_BACKWARD)
        if not forward:
            return
        step = forward * self.walk_speed * dt
        x, y, z = self.position
        # wizwalker's yaw: 0 faces -y, counterclockwise.
        self.position = (x - math.sin(self.yaw) * step, y - math.cos(self.yaw) * step, z)

    def place(self, position: tuple[float, float, float], zone: str | None = None) -> None:
        """Move the wizard as the server would: a port, a door, a reset."""
        self.advance()
        self.position = position
        if zone is not None:
            self.zone = zone

    def log_out(self) -> None:
        """Free the client object, as a logout does: the hook still points at the memory."""
        self.type_name = ""
        self.character = 0xFFFFFFFFFFFFFFFF
        self.speed = 0x1234

    def presses(self, action: GameAction) -> int:
        code = self._keys.choose(action).code
        return sum(1 for r in self.inputs if r.kind == "press" and r.detail == code)

    # -- what a real client answers ---------------------------------------------------

    async def send_key(self, key, seconds: float = 0) -> None:
        self.advance()
        code = key.value
        self.log("press", code)
        if self.on_press is not None:
            self.on_press(self._actions.get(code, code))

    async def zone_name(self) -> str | None:
        return self.zone

    async def is_loading(self) -> bool:
        return self.loading

    async def in_battle(self) -> bool:
        return self.battle

    async def is_in_npc_range(self) -> bool:
        return self.npc_range

    async def is_in_dialog(self) -> bool:
        return self.dialog

    # -- what the client manager asks of a client ---------------------------------------

    @property
    def window_rectangle(self):
        """Where the window stands on screen: all at the origin, so slot order decides."""
        return _Rect()

    @property
    def process_id(self) -> int:
        return 0

    async def activate_hooks(self) -> None:
        """Hooks a simulated client at once: nothing waits for a character in the world."""
        self.hooked = True

    async def close(self) -> None:
        """Takes the hooks off, leaving the window: what an unhook does."""
        self.hooked = False


@dataclass(frozen=True)
class _Rect:
    x1: int = 0
    y1: int = 0


class SimHandler:
    """A stand-in for wizwalker's handler, holding :class:`SimClient` windows.

    What :class:`~bartleby.runtime.clients.client_manager.ClientManager` polls: ``clients``
    are the open windows, each handed out once by ``get_new_clients``, and ``close_window``
    takes one away as a crash or a quit would.
    """

    def __init__(self, clients: list[SimClient] | None = None) -> None:
        self.clients: list[SimClient] = []
        self._announced: set[int] = set()
        for client in clients or []:
            self.open_window(client)

    def open_window(self, client: SimClient) -> None:
        self.clients.append(client)

    def close_window(self, client: SimClient) -> None:
        self.clients = [c for c in self.clients if c is not client]

    def remove_dead_clients(self) -> None:
        pass

    def get_new_clients(self) -> list[SimClient]:
        new = [c for c in self.clients if c.window_handle not in self._announced]
        self._announced.update(c.window_handle for c in new)
        return new
