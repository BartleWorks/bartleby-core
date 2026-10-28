"""Everything that sends input to a client: keys, held keys, and the memory writes that steer.

A :class:`ClientInput` is only ever handed out inside an arbiter lease
(:mod:`bartleby.runtime.arbiter`), which is how no feature presses a key on a client it
does not own at that moment. Memory *reads* need no lease and do not come through here.

Keys are pressed by the account's own binding (:func:`~bartleby.runtime.clients.game_keys.press`),
never by a fixed letter, since every account binds them differently (BartleBot's
``docs/KEY_BINDINGS.md``). A held key is sent as window messages, key-down repeated every
:data:`~bartleby.runtime.timing.HELD_KEY_REPEAT` as a held key repeats, which is how
BartleBot's walking probe walks (``tools/probe_walk_input.py``); the game window need not
have the focus, so a key held on one client never reaches another.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction, action_label
from bartleby.runtime.timing import HELD_KEY_REPEAT

logger = logging.getLogger(__name__)

#: The game's speed field at 2.0x: a percentage bonus that already holds a mount's, so 100
#: is 2.0x in total whatever the mount. The engine allows 2.0x; nothing writes above it
#: (BartleBot's docs/WALKING.md, "The speed multiplier may be written, up to 2.0x").
SPEED_VALUE_MAX = 100


def speed_value(multiplier: float) -> int:
    """The speed field for walking *multiplier* times as fast in total, between 1.0x and 2.0x."""
    return max(0, min(round((multiplier - 1.0) * 100), SPEED_VALUE_MAX))


class KeySender(Protocol):
    """Sends a key's down and up to one client, by virtual-key code."""

    async def down(self, code: int) -> None: ...

    async def up(self, code: int) -> None: ...


class WindowKeys:
    """Key-down and key-up sent to a game window as messages, which needs no focus."""

    def __init__(self, hwnd: int) -> None:
        self.hwnd = hwnd

    async def down(self, code: int) -> None:
        from wizwalker.constants import WM_KEYDOWN, user32  # noqa: PLC0415 - keeps the module importable without a game
        from wizwalker.window_messages import send_message  # noqa: PLC0415

        await send_message(user32.SendMessageW, self.hwnd, WM_KEYDOWN, code, 0)

    async def up(self, code: int) -> None:
        from wizwalker.constants import WM_KEYUP, user32  # noqa: PLC0415
        from wizwalker.window_messages import send_message  # noqa: PLC0415

        await send_message(user32.SendMessageW, self.hwnd, WM_KEYUP, code, 0)


def key_sender_for(client) -> KeySender:
    """The client's own sender when it has one (a simulated client), else its window's."""
    sender = getattr(client, "key_sender", None)
    return sender if sender is not None else WindowKeys(client.window_handle)


class ClientInput:
    """The input half of one client, valid while the lease that made it is held.

    Every method raises :class:`InputClosed` once the lease has ended, so a task that kept a
    reference past its ``async with`` cannot press anything.
    """

    def __init__(self, client, keys: KeySender | None = None) -> None:
        self.client = client
        self._keys = keys or key_sender_for(client)
        self._held: dict[GameAction, int] = {}
        self._repeat: asyncio.Task | None = None
        self._closed = False

    @property
    def held(self) -> frozenset[GameAction]:
        return frozenset(self._held)

    def _check(self) -> None:
        if self._closed:
            raise InputClosed("this lease has ended")

    async def press(self, action: GameAction, seconds: float = 0) -> None:
        """Press the key the account has *action* on."""
        self._check()
        await game_keys.press(self.client, action, seconds)

    async def _code(self, action: GameAction) -> int:
        choice = (await game_keys.key_map(self.client)).choose(action)
        if choice.code is None:
            raise game_keys.KeyNotPressable(f"{action_label(action)} {choice.problem}")
        return choice.code

    async def hold(self, action: GameAction) -> bool:
        """Hold *action*'s key down until :meth:`release`. True when it was not already held."""
        self._check()
        if action in self._held:
            return False
        code = await self._code(action)
        self._held[action] = code
        await self._keys.down(code)
        if self._repeat is None or self._repeat.done():
            self._repeat = asyncio.create_task(self._repeat_held())
        return True

    async def release(self, action: GameAction) -> None:
        code = self._held.pop(action, None)
        if code is not None:
            await self._keys.up(code)

    async def release_all(self) -> None:
        """Let go of every held key. Safe to call more than once, and after the lease ends."""
        task, self._repeat = self._repeat, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        held, self._held = self._held, {}
        for code in held.values():
            try:
                await self._keys.up(code)
            except Exception:  # noqa: BLE001 - a closed window has no key to let go of
                logger.debug("Could not release a key on window %s", getattr(self.client, "window_handle", "?"))

    async def _repeat_held(self) -> None:
        while self._held:
            await asyncio.sleep(HELD_KEY_REPEAT)
            for code in list(self._held.values()):
                await self._keys.down(code)

    async def write_yaw(self, yaw: float) -> None:
        """Face *yaw*. The server checks where a wizard is, not which way it faces."""
        self._check()
        await self.client.body.write_yaw(yaw)

    async def write_speed(self, value: int) -> None:
        """Write the speed field, never above :data:`SPEED_VALUE_MAX`."""
        self._check()
        await self.client.client_object.write_speed_multiplier(min(value, SPEED_VALUE_MAX))

    async def close(self) -> None:
        self._closed = True
        await self.release_all()


class InputClosed(RuntimeError):
    """Input was sent through a lease that has ended."""
