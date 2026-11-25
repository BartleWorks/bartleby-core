"""Everything that sends input to a client: keys, held keys, and the memory writes that steer.

A :class:`ClientInput` is only ever handed out inside an arbiter lease
(:mod:`bartleby.runtime.arbiter`), which is how no feature presses a key on a client it
does not own at that moment. Memory *reads* need no lease and do not come through here.

Keys are pressed by the account's own binding (:func:`~bartleby.runtime.clients.game_keys.press`),
never by a fixed letter, since every account binds them differently (BartleBot's
``docs/KEY_BINDINGS.md``). A held key is sent as window messages, key-down repeated every
:data:`~bartleby.runtime.timing.HELD_KEY_REPEAT` as a held key repeats; the game window need
not have the focus, so a key held on one client never reaches another.

**A held event loop lets go of the keys.** The game walks a wizard on for as long as forward is
held, and with the loop held nothing steers it: about 600 units a second on foot. So a watchdog
thread (:class:`_Watchdog`) looks at every client holding a key, and once the loop has fallen
:data:`~bartleby.runtime.timing.HELD_KEY_LOOP_LATE` behind its repeat, sends key-up for each of
them from its own thread and forgets them, so they are not repeated when the loop comes back.
The holder finds them no longer :attr:`ClientInput.held` and holds them again when it means to.
The watchdog only ever lets a key go, never presses one.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Protocol

from bartleby.runtime.clients import game_keys
from bartleby.runtime.game.key_bindings import GameAction, action_label
from bartleby.runtime.timing import (
    HELD_KEY_LOOP_LATE,
    HELD_KEY_RELEASE_TIMEOUT,
    HELD_KEY_REPEAT,
    HELD_KEY_WATCH_INTERVAL,
)

logger = logging.getLogger(__name__)

#: The game's speed field at 2.0x: a percentage bonus that already holds a mount's, so 100
#: is 2.0x in total whatever the mount. The engine allows 2.0x; nothing writes above it
#: (``docs/WIZARD101.md``, *Moving*).
SPEED_VALUE_MAX = 100


def speed_value(multiplier: float) -> int:
    """The speed field for walking *multiplier* times as fast in total, between 1.0x and 2.0x."""
    return max(0, min(round((multiplier - 1.0) * 100), SPEED_VALUE_MAX))


class KeySender(Protocol):
    """Sends a key's down and up to one client, by virtual-key code."""

    async def down(self, code: int) -> None: ...

    async def up(self, code: int) -> None: ...

    def up_now(self, code: int) -> None:
        """Send key-up from whatever thread calls, waiting on the game a short while at most."""
        ...


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

    def up_now(self, code: int) -> None:
        """Key-up sent from the calling thread, as the window's worker sends every other message,
        given up on after :data:`~bartleby.runtime.timing.HELD_KEY_RELEASE_TIMEOUT` if the window
        is not answering (it takes it once it answers again)."""
        import ctypes  # noqa: PLC0415 - Windows only, and only once a loop is held
        from ctypes import wintypes  # noqa: PLC0415

        from wizwalker.constants import WM_KEYUP  # noqa: PLC0415

        send = ctypes.windll.user32.SendMessageTimeoutW
        send.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        )
        send(self.hwnd, WM_KEYUP, code, 0, _SMTO_ABORTIFHUNG, int(HELD_KEY_RELEASE_TIMEOUT * 1000), None)


#: SendMessageTimeoutW's flag to return at once from a window Windows already counts as hung.
_SMTO_ABORTIFHUNG = 0x0002


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
        # The watchdog's thread reads and empties _held; the loop changes it under this lock too.
        self._lock = threading.Lock()
        self._beat = time.monotonic()
        #: How many times the watchdog has let go of this input's keys, for a holder to notice.
        self.dropped = 0

    @property
    def held(self) -> frozenset[GameAction]:
        with self._lock:
            return frozenset(self._held)

    def _drop_if_late(self, now: float) -> None:
        """The watchdog's look, from its thread: let go of every held key if the loop is late."""
        with self._lock:
            late = now - self._beat
            if not self._held or late <= HELD_KEY_LOOP_LATE:
                return
            codes, self._held = list(self._held.values()), {}
            self.dropped += 1
        logger.warning(
            "Let go of %d held key(s) on window %s: the event loop is %.2f s behind",
            len(codes),
            getattr(self.client, "window_handle", "?"),
            late,
        )
        for code in codes:
            try:
                self._keys.up_now(code)
            except Exception:  # noqa: BLE001 - a closed window has no key to let go of
                logger.debug(
                    "The watchdog could not release a key on window %s", getattr(self.client, "window_handle", "?")
                )

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
        if action in self.held:
            return False
        code = await self._code(action)
        with self._lock:
            self._beat = time.monotonic()
            self._held[action] = code
        _watchdog.watch(self)
        await self._keys.down(code)
        if self._repeat is None or self._repeat.done():
            self._repeat = asyncio.create_task(self._repeat_held())
        return True

    async def release(self, action: GameAction) -> None:
        with self._lock:
            code = self._held.pop(action, None)
        if code is not None:
            await self._keys.up(code)

    async def release_all(self) -> None:
        """Let go of every held key. Safe to call more than once, and after the lease ends.

        The repeat is waited for with ``asyncio.wait``, which never raises its end: a cancel
        that reaches the caller in that wait is the caller's, and goes on up, where suppressing
        CancelledError swallowed it and left a stopped follower walking. The keys are let go
        either way.
        """
        task, self._repeat = self._repeat, None
        try:
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                await asyncio.wait([task])
        finally:
            with self._lock:
                held, self._held = self._held, {}
            _watchdog.unwatch(self)
            for code in held.values():
                try:
                    await self._keys.up(code)
                except Exception:  # noqa: BLE001 - a closed window has no key to let go of
                    logger.debug("Could not release a key on window %s", getattr(self.client, "window_handle", "?"))

    async def _repeat_held(self) -> None:
        """Repeat every held key, and beat for the watchdog each time the loop gets here."""
        try:
            while True:
                await asyncio.sleep(HELD_KEY_REPEAT)
                with self._lock:
                    self._beat = time.monotonic()
                    codes = list(self._held.values())
                if not codes:
                    return
                for code in codes:
                    with self._lock:
                        still = code in self._held.values()
                    if still:  # not let go by the watchdog while an earlier one was sent
                        await self._keys.down(code)
        finally:
            with self._lock:
                if not self._held:
                    _watchdog.unwatch(self)

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


class _Watchdog:
    """One thread for every :class:`ClientInput` holding a key, running only while one does."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._inputs: set[ClientInput] = set()
        self._thread: threading.Thread | None = None

    def watch(self, client_input: ClientInput) -> None:
        with self._lock:
            self._inputs.add(client_input)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="held-key-watchdog", daemon=True)
                self._thread.start()

    def unwatch(self, client_input: ClientInput) -> None:
        with self._lock:
            self._inputs.discard(client_input)

    def watching(self) -> int:
        with self._lock:
            return len(self._inputs)

    def _run(self) -> None:
        while True:
            time.sleep(HELD_KEY_WATCH_INTERVAL)
            with self._lock:
                inputs = list(self._inputs)
                if not inputs:
                    self._thread = None
                    return
            now = time.monotonic()
            for client_input in inputs:
                client_input._drop_if_late(now)  # noqa: SLF001 - the watchdog is ClientInput's own


_watchdog = _Watchdog()
