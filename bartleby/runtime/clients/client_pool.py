"""The game clients at run time: found, hooked, named by account and kept in slot order.

The part of BartleBot's ``ClientManager`` every product needs, without Qt and without a
bot: it polls wizwalker's handler for new windows, hooks each in the background, matches
each window to an account by the title the launcher gives it, and publishes the hooked
clients in slot order to whoever listens. BartleBot's recovery, relogging and drop
watching stay there for now; the plan has BartleBuddy's recovery runtime arrive with
Autopilot.

Two rules carried over, each the cost of a bug:

* **Hooking runs in the background.** ``activate_hooks()`` waits for a character to be in
  the world, with no timeout, so a window at character select would stall the loop for
  good. And it is never retried: a failed activation leaves jumps into wizwalker's
  scratch region, which a fresh attempt would zero out from under them.
* **A failed read keeps the list as it was.** The next pass compares against it, and that
  comparison is what notices a client that died.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

from bartleby.runtime.clients import game_keys
from bartleby.runtime.clients.credentials import CredentialStore
from bartleby.runtime.timing import CLIENT_DETECT_ERROR_BACKOFF, CLIENT_DETECT_INTERVAL, CLIENT_DETECT_INTERVAL_IDLE

logger = logging.getLogger(__name__)

#: Called with the hooked clients, in slot order, whenever that list changes.
ClientsListener = Callable[[list], None]


def launched_title(display: str) -> str:
    """The title the launcher gives an account's window: ``[<alias or censored name>] Wizard101``."""
    return f"[{display}] Wizard101"


class ClientPool:
    """Hooks clients as they appear and keeps them in slot order."""

    def __init__(self, handler=None, *, accounts: Callable[[], list[dict]] = list) -> None:
        if handler is None:
            from wizwalker.extensions.wizsprinter import WizSprinter  # noqa: PLC0415

            handler = WizSprinter()
        self._handler = handler
        self._accounts = accounts
        self.clients: list = []
        self._order: list[str] = []
        self._accounts_by_hwnd: dict[int, str] = {}
        self._activating: set[int] = set()
        self._failed: set[int] = set()
        self._tasks: set[asyncio.Task] = set()
        self._loop_task: asyncio.Task | None = None
        self._listeners: list[ClientsListener] = []

    # -- listening -------------------------------------------------------------------------

    def subscribe(self, listener: ClientsListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _publish(self) -> None:
        for listener in list(self._listeners):
            try:
                listener(list(self.clients))
            except Exception:
                logger.exception("ClientPool listener raised")

    # -- slots ---------------------------------------------------------------------------------

    def reserve(self, usernames: list[str]) -> None:
        """Put these accounts first, in this order: the launch that is about to start them."""
        self._order = list(usernames) + [u for u in self._order if u not in usernames]

    def account_for(self, client) -> str | None:
        return self._accounts_by_hwnd.get(client.window_handle)

    def _ordered(self, clients: list) -> list:
        """Reserved accounts in their order, then everyone else by where their window stands."""

        def key(client):
            username = self._accounts_by_hwnd.get(client.window_handle)
            if username in self._order:
                return (0, self._order.index(username), 0, 0)
            rect = getattr(client, "window_rectangle", None)
            return (1, 0, getattr(rect, "y1", 0), getattr(rect, "x1", 0))

        return sorted(clients, key=key)

    def _match_titles(self, clients: list) -> None:
        from wizwalker.utils import get_window_title  # noqa: PLC0415

        titles = {}
        for account in self._accounts():
            display = CredentialStore.display_name(account["username"], account.get("alias") or None)
            titles[launched_title(display)] = account["username"]
        for client in clients:
            if client.window_handle in self._accounts_by_hwnd:
                continue
            with contextlib.suppress(Exception):
                if username := titles.get(get_window_title(client.window_handle)):
                    self._accounts_by_hwnd[client.window_handle] = username

    # -- the loop ------------------------------------------------------------------------------

    def start(self) -> None:
        if self._loop_task is None or self._loop_task.done():
            self._loop_task = asyncio.create_task(self._loop(), name="client-pool")

    async def stop(self) -> None:
        task, self._loop_task = self._loop_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _loop(self) -> None:
        while True:
            try:
                await self.poll()
                await asyncio.sleep(CLIENT_DETECT_INTERVAL if self.clients else CLIENT_DETECT_INTERVAL_IDLE)
            except Exception:
                logger.exception("Client pool error")
                await asyncio.sleep(CLIENT_DETECT_ERROR_BACKOFF)

    async def poll(self) -> None:
        """One pass: reap closed windows, start hooking new ones, publish the hooked in order."""
        self._handler.remove_dead_clients()
        for client in self._handler.get_new_clients():
            self._activate(client)
        live = list(self._handler.clients)
        live_hwnds = {c.window_handle for c in live}
        self._failed &= live_hwnds
        for hwnd in list(self._accounts_by_hwnd):
            if hwnd not in live_hwnds:
                del self._accounts_by_hwnd[hwnd]
        self._match_titles(live)
        hooked = [c for c in live if c.window_handle not in self._activating | self._failed]
        ordered = self._ordered(hooked)
        if [c.window_handle for c in ordered] != [c.window_handle for c in self.clients]:
            self.clients = ordered
            self._publish()

    def _activate(self, client) -> None:
        hwnd = client.window_handle
        if hwnd in self._activating:
            return
        self._activating.add(hwnd)

        async def activate() -> None:
            try:
                await client.activate_hooks()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Hooking window %s failed; it is left alone until it closes", hwnd)
                self._failed.add(hwnd)
            else:
                # The account's key bindings are in memory once a character is in the world:
                # find them now, rather than on the first press.
                self._spawn(game_keys.key_map(client, warn=False))
            finally:
                self._activating.discard(hwnd)

        self._spawn(activate())

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        """Stop polling and unhook every client, leaving the game windows open."""
        await self.stop()
        for task in list(self._tasks):
            task.cancel()
        for client in list(self._handler.clients):
            with contextlib.suppress(Exception):
                await client.close()
        self.clients = []
        self._publish()
