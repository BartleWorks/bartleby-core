"""A simulated client: a real client's interface, with no game behind it.

The arbiter, following, spacing and the quester are where most bugs will be, and none of
them should need the game to test. A :class:`SimClient` answers the reads they make
(position, yaw, zone, loading, battle, the speed field, the interact prompt), takes the
input they send (keys pressed and held, yaw and speed written), logs every input with the
time it came, and walks while its forward key is held, at the speed its speed field says.

Movement is integrated on demand against a clock the test can drive (:class:`SimClock`),
so a test runs as fast as it likes and lands where the arithmetic says. Walking speed is
600 units a second on foot, as measured (BartleBot's docs/WALKING.md), times the speed
field's bonus.
"""

from __future__ import annotations

import itertools
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from wizwalker import XYZ

from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction, KeyMap

#: Units a second on foot, on flat ground.
WALK_SPEED = 600.0

_handles = itertools.count(50_000)


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
    kind: str  # "press", "down", "up", "yaw", "speed"
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


@dataclass(eq=False)
class SimClient:
    """One simulated game client. Set its fields to stage a scene; read ``inputs`` after."""

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    yaw: float = 0.0
    zone: str | None = "WizardCity/WC_Hub"
    loading: bool = False
    battle: bool = False
    speed: int = 0
    npc_range: bool = False
    dialog: bool = False
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
