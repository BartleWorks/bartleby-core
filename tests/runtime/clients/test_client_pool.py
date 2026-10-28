"""The client pool: hooks in the background, publishes the hooked in slot order."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from bartleby.runtime.clients.client_pool import ClientPool, launched_title


class FakeWindow:
    def __init__(self, hwnd, y=0, x=0, hooks=None):
        self.window_handle = hwnd
        self.window_rectangle = SimpleNamespace(x1=x, y1=y)
        self._hooks = hooks or asyncio.Event()
        self.closed = False

    async def activate_hooks(self):
        await self._hooks.wait()

    async def close(self):
        self.closed = True


class FakeHandler:
    def __init__(self):
        self.clients = []
        self._new = []

    def add(self, window):
        self.clients.append(window)
        self._new.append(window)

    def get_new_clients(self):
        new, self._new = self._new, []
        return new

    def remove_dead_clients(self):
        pass


async def _settle():
    for _ in range(3):
        await asyncio.sleep(0)


async def test_a_client_is_published_only_once_hooked():
    handler = FakeHandler()
    pool = ClientPool(handler)
    heard = []
    pool.subscribe(heard.append)
    window = FakeWindow(1)
    handler.add(window)
    with patch("bartleby.runtime.clients.client_pool.game_keys.key_map"):
        await pool.poll()
        assert pool.clients == []
        window._hooks.set()
        await _settle()
        await pool.poll()
    assert pool.clients == [window]
    assert heard == [[window]]


async def test_a_window_at_character_select_does_not_hold_up_the_rest():
    handler = FakeHandler()
    pool = ClientPool(handler)
    stuck, ready = FakeWindow(1), FakeWindow(2)
    ready._hooks.set()
    handler.add(stuck)
    handler.add(ready)
    with patch("bartleby.runtime.clients.client_pool.game_keys.key_map"):
        await pool.poll()
        await _settle()
        await pool.poll()
    assert pool.clients == [ready]


async def test_reserved_accounts_come_first_in_their_order_then_by_window_position():
    handler = FakeHandler()
    accounts = [{"username": "alpha"}, {"username": "bravo"}]
    pool = ClientPool(handler, accounts=lambda: accounts)
    a, b, stray = FakeWindow(1, y=500), FakeWindow(2, y=0), FakeWindow(3, y=10)
    for w in (a, b, stray):
        w._hooks.set()
        handler.add(w)
    titles = {1: launched_title("a***a"), 2: launched_title("b***o"), 3: "Wizard101"}
    pool.reserve(["bravo", "alpha"])
    with (
        patch("wizwalker.utils.get_window_title", side_effect=lambda h: titles[h]),
        patch("bartleby.runtime.clients.client_pool.game_keys.key_map"),
    ):
        await pool.poll()
        await _settle()
        await pool.poll()
    assert pool.clients == [b, a, stray]
    assert pool.account_for(a) == "alpha"


async def test_close_unhooks_every_client_and_publishes_none():
    handler = FakeHandler()
    pool = ClientPool(handler)
    w = FakeWindow(1)
    w._hooks.set()
    handler.add(w)
    heard = []
    pool.subscribe(heard.append)
    with patch("bartleby.runtime.clients.client_pool.game_keys.key_map"):
        await pool.poll()
        await _settle()
        await pool.poll()
    await pool.close()
    assert w.closed and pool.clients == [] and heard[-1] == []
