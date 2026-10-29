"""Releasing the clients on the way out: what ``ClientManager.shutdown`` does to them.

Closing BartleBot used to destroy every embedded game window: ``ClientTile`` re-parents
each one under the Qt window, so Qt took its children down with it, and nothing unhooked
on the way out. BartleBot's quit gate, the other half of the fix, is tested there.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from bartleby.runtime.clients.client_manager import ClientManager


def _client(hwnd: int, pid: int, log: list) -> MagicMock:
    client = MagicMock()
    client.window_handle = hwnd
    client.process_id = pid
    client.close = AsyncMock(side_effect=lambda: log.append(("unhook", hwnd)))
    return client


def _manager(hooked: list, unhooked: list, game_view: MagicMock | None = None) -> ClientManager:
    manager = ClientManager(handler=MagicMock(), settings=MagicMock(accounts=[]), game_view=game_view or MagicMock())
    manager.clients.extend(hooked)
    manager.unhooked_clients.extend(unhooked)
    return manager


# ------------------------------------------------------------------ ClientManager.shutdown


async def test_leaving_the_windows_open_unhooks_and_vacates_every_tile(monkeypatch):
    log: list = []
    closed: list = []
    monkeypatch.setattr(
        "bartleby.runtime.clients.client_manager.force_close_client",
        AsyncMock(side_effect=lambda hwnd, pid=None: closed.append(hwnd) or True),
    )
    game_view = MagicMock()
    manager = _manager([_client(1, 11, log), _client(2, 22, log)], [_client(3, 33, log)], game_view)

    survivors = await manager.shutdown(close_windows=False)

    assert survivors == []
    assert closed == [], "leaving the windows open must not close any of them"
    # Only the hooked pair had hooks to release; the third was already unhooked.
    assert sorted(log) == [("unhook", 1), ("unhook", 2)]
    assert manager.clients == []
    # The embed is what kills the windows, so releasing it is the point of the whole path.
    game_view.unembed_all.assert_called_once()


async def test_closing_the_windows_unhooks_first_and_takes_the_unhooked_ones_too(monkeypatch):
    log: list = []
    monkeypatch.setattr(
        "bartleby.runtime.clients.client_manager.force_close_client",
        AsyncMock(side_effect=lambda hwnd, pid=None: log.append(("close", hwnd, pid)) or True),
    )
    manager = _manager([_client(1, 11, log)], [_client(3, 33, log)])

    survivors = await manager.shutdown(close_windows=True)

    assert survivors == []
    # Hooks come off before the window goes: a terminated process with hooks still
    # installed is the state the release exists to avoid.
    assert log[0] == ("unhook", 1)
    assert sorted(log[1:]) == [("close", 1, 11), ("close", 3, 33)]


async def test_a_window_that_will_not_close_is_reported_rather_than_waited_on(monkeypatch):
    monkeypatch.setattr(
        "bartleby.runtime.clients.client_manager.force_close_client",
        AsyncMock(side_effect=lambda hwnd, pid=None: hwnd != 2),
    )
    manager = _manager([_client(1, 11, []), _client(2, 22, [])], [])

    assert await manager.shutdown(close_windows=True) == [2]


async def test_the_detection_loop_is_stopped_before_anything_is_released(monkeypatch):
    """A tick landing mid-shutdown would re-hook and re-embed what was just let go."""
    order: list = []
    manager = _manager([_client(1, 11, order)], [])
    monkeypatch.setattr("bartleby.runtime.clients.client_manager.force_close_client", AsyncMock(return_value=True))

    async def _loop():
        await asyncio.Event().wait()

    task = asyncio.ensure_future(_loop())
    manager._client_loop_task = task

    await manager.shutdown(close_windows=False)

    assert task.cancelled() or task.cancelling()
    assert order == [("unhook", 1)]


async def test_a_failing_unhook_still_releases_the_windows(monkeypatch):
    game_view = MagicMock()
    manager = _manager([_client(1, 11, [])], [], game_view)
    monkeypatch.setattr(manager, "unhook_all", AsyncMock(side_effect=RuntimeError("hook gone")))

    assert await manager.shutdown(close_windows=False) == []
    game_view.unembed_all.assert_called_once()
