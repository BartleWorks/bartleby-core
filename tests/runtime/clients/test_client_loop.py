"""Tests for ClientManager's hook-activation path in _client_loop.

The point of these tests is the one property the loop has to keep: a client whose
activate_hooks() never returns must not hold up anything else. WizWalker's
activate_hooks() waits for the player struct with no timeout, and a window parked on the
login or character-select screen never writes it — which is the normal state of every
window auto-select is driving. Before the fix the loop awaited that inline and stopped
ticking entirely.

Like BartleBot's tests/clients/test_recovery.py, the harness binds real ClientManager methods to a minimal
fake object so the production code runs without a QApplication, WizSprinter, or any
Wizard101 process.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from bartleby.runtime import run_trace
from bartleby.runtime.clients import account_identity
from bartleby.runtime.clients.client_manager import ClientManager
from bartleby.runtime.launcher.login_gate import gate

# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class FakeHookClient:
    """Stand-in for a WizWalker Client whose hook activation we control."""

    def __init__(self, hwnd: int) -> None:
        self.window_handle = hwnd
        self.window_rectangle = MagicMock(x1=0, y1=hwnd)
        # Resolve this to let activate_hooks() return; leave it pending to model a client
        # sitting at character select; set an exception to model a failed install.
        self.hooks_ready: asyncio.Future = asyncio.get_event_loop().create_future()
        self.activate_calls = 0

    async def activate_hooks(self) -> None:
        self.activate_calls += 1
        await self.hooks_ready


class FakeHandler:
    """Stand-in for WizSprinter: hands out new clients once each."""

    def __init__(self) -> None:
        self.clients: list = []
        self._announced: set[int] = set()

    def add(self, client) -> None:
        self.clients.append(client)

    def remove_dead_clients(self) -> None:
        pass

    def get_new_clients(self) -> list:
        new = [c for c in self.clients if c.window_handle not in self._announced]
        self._announced.update(c.window_handle for c in new)
        return new


class LoopHarness:
    """Binds the real loop and activation methods; stubs the parts under no test."""

    _client_loop = ClientManager._client_loop
    _activate_ungated = ClientManager._activate_ungated
    _start_activation = ClientManager._start_activation
    _build_ordered_clients = ClientManager._build_ordered_clients
    _assign_slot = ClientManager._assign_slot
    _place_account = ClientManager._place_account
    _forget_closed_windows = ClientManager._forget_closed_windows
    # The identity pass is real, but reads nothing while _settings has no accounts.
    _learn_identities = ClientManager._learn_identities
    _identity_task_done = ClientManager._identity_task_done
    _learn_identity = ClientManager._learn_identity
    _learn_fingerprint = ClientManager._learn_fingerprint
    _adopt_by_identity = ClientManager._adopt_by_identity
    _read_user_id = ClientManager._read_user_id
    stop = ClientManager.stop
    has_client_titled = ClientManager.has_client_titled
    client_titled = ClientManager.client_titled

    def __init__(self) -> None:
        self._handler = FakeHandler()
        self._clients: list = []
        self._unhooked_clients: list = []
        self._unhooked_hwnds: set[int] = set()
        self._activating_hwnds: set[int] = set()
        self._gated_clients: list = []
        self._failed_hook_hwnds: set[int] = set()
        self._activation_tasks: set[asyncio.Task] = set()
        self._identity_tasks: dict[int, asyncio.Task] = {}
        self._client_loop_task: asyncio.Task | None = None
        self._settings = MagicMock(accounts=[])
        self._hwnd_account_map: dict[int, str] = {}
        self._hwnd_titles: dict[int, str] = {}
        self._hwnd_user_ids: dict[int, int] = {}
        self._client_order: list[str | None] = []
        self._pending_recovery: dict[int, str] = {}
        self._vacated_slots: dict[str, int] = {}

        self.clients_changed = MagicMock()
        self.all_clients_changed = MagicMock()
        self._refresh_account_map = MagicMock()
        self._game_view = MagicMock()
        self._sync_tiles = MagicMock()
        self._place_pending_accounts = MagicMock()
        self._on_clients_removed = MagicMock()
        self._refresh_wizard_names = AsyncMock()
        self._learn_character_roster = MagicMock()

    def start(self) -> None:
        self._client_loop_task = asyncio.create_task(self._client_loop())

    @property
    def published_hwnds(self) -> set[int]:
        return {c.window_handle for c in self._clients}


# Real cadences are 1 s / 3 s; compressed here so the suite does not spend half a minute
# sleeping. The loop reads these as module globals at each call, so patching lands.
_TEST_INTERVAL = 0.01


async def _tick(cycles: float = 4) -> None:
    """Let the loop run for a few of its detection cycles."""
    await asyncio.sleep(_TEST_INTERVAL * cycles)


@pytest.fixture(autouse=True)
def _fast_loop(monkeypatch):
    for name in (
        "CLIENT_DETECT_INTERVAL",
        "CLIENT_DETECT_INTERVAL_IDLE",
        "CLIENT_DETECT_ERROR_BACKOFF",
    ):
        monkeypatch.setattr(f"bartleby.runtime.clients.client_manager.{name}", _TEST_INTERVAL)


@pytest.fixture
def harness() -> LoopHarness:
    return LoopHarness()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_pending_client_is_not_published(harness):
    """A client whose hooks never come up stays out of the live client list."""
    stuck = FakeHookClient(100)
    harness._handler.add(stuck)
    harness.start()
    try:
        await _tick()
        assert harness.published_hwnds == set()
        assert harness._activating_hwnds == {100}
    finally:
        harness.stop()


async def test_pending_client_does_not_block_another(harness):
    """The stall regression: one client stuck at character select must not gate the rest.

    Before the fix these two were awaited together in a single gather, so the ready
    client was invisible to the app until the stuck one logged a character in.
    """
    stuck = FakeHookClient(100)
    ready = FakeHookClient(200)
    harness._handler.add(stuck)
    harness._handler.add(ready)
    harness.start()
    try:
        ready.hooks_ready.set_result(None)
        await _tick()
        assert harness.published_hwnds == {200}
        assert harness._activating_hwnds == {100}
    finally:
        harness.stop()


async def test_client_is_published_once_hooks_come_up(harness):
    """The auto-select completion signal: the client appears when its character loads in."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness.start()
    try:
        await _tick(3)
        assert harness.published_hwnds == set()

        client.hooks_ready.set_result(None)
        await _tick(3)
        assert harness.published_hwnds == {100}
        assert harness._activating_hwnds == set()
    finally:
        harness.stop()


async def test_a_client_s_key_bindings_are_read_once_its_hooks_come_up(harness, monkeypatch):
    """The scan for a client's key bindings takes seconds, so it is begun as the client
    arrives, quietly and in the background, rather than when the first run starts; and publishing
    never waits on it."""
    from bartleby.runtime.clients import game_keys, heap_scan  # noqa: PLC0415

    reads: list[tuple[int, bool, bool, heap_scan.Priority]] = []
    scan = asyncio.Event()

    async def key_map(client, *, refresh=False, warn=True, priority=heap_scan.Priority.URGENT):
        reads.append((client.window_handle, refresh, warn, priority))
        await scan.wait()

    monkeypatch.setattr(game_keys, "key_map", key_map)
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness.start()
    try:
        await _tick(2)
        assert reads == []
        client.hooks_ready.set_result(None)
        await _tick(3)
        # Refreshed: a reattached client is tried again. Background: its resize goes first.
        assert reads == [(100, True, False, heap_scan.Priority.BACKGROUND)]
        assert harness.published_hwnds == {100}  # while the scan is still going
    finally:
        scan.set()
        harness.stop()


async def test_failed_activation_is_not_published_or_retried(harness):
    """A failed hook install is abandoned, not retried.

    activate_all_hooks() installs every hook before waiting, so a client that failed
    mid-wait has left jmps pointing into the autobot region. Building a fresh Client for
    the same process would zero that region out from under them, so the client is left
    registered with the handler and simply never published.
    """
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness.start()
    try:
        client.hooks_ready.set_exception(RuntimeError("pattern scan failed"))
        await _tick()
        assert harness.published_hwnds == set()
        assert harness._failed_hook_hwnds == {100}
        assert client.activate_calls == 1
    finally:
        harness.stop()


async def test_failed_hwnd_is_forgotten_when_window_closes(harness):
    """_failed_hook_hwnds must not grow without bound across a long session."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness.start()
    try:
        client.hooks_ready.set_exception(RuntimeError("boom"))
        await _tick()
        assert harness._failed_hook_hwnds == {100}

        harness._handler.clients.clear()
        await _tick()
        assert harness._failed_hook_hwnds == set()
    finally:
        harness.stop()


async def test_a_closed_window_leaves_the_account_map(harness):
    """Issue #65: nothing unwrote the account map, so a closed window kept its account
    in active_accounts for the rest of the session and begin_launch refused to relaunch it."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness._hwnd_account_map[100] = "alpha"
    harness.start()
    try:
        client.hooks_ready.set_result(None)
        await _tick()
        assert harness._hwnd_account_map == {100: "alpha"}

        harness._handler.clients.clear()
        await _tick()
        assert harness._hwnd_account_map == {}
    finally:
        harness.stop()


async def test_a_closed_window_is_still_reported_as_removed_first(harness):
    """The map is what names the account recovery relaunches, so it is dropped after
    _on_clients_removed has read it, never before."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    harness._hwnd_account_map[100] = "alpha"
    harness.start()
    try:
        client.hooks_ready.set_result(None)
        await _tick()
        seen: list[dict] = []
        harness._on_clients_removed = lambda hwnds: seen.append(dict(harness._hwnd_account_map))

        harness._handler.clients.clear()
        await _tick()
        assert seen == [{100: "alpha"}]
    finally:
        harness.stop()


async def test_a_failed_pass_keeps_the_list_so_a_death_is_still_reported(harness):
    """A dying client's last reads fail the pass; the next pass must still report it removed (#154)."""
    from wizwalker.errors import MemoryReadError

    alive, dying = FakeHookClient(100), FakeHookClient(200)
    for client in (alive, dying):
        harness._handler.add(client)
        client.hooks_ready.set_result(None)
    harness.start()
    try:
        await _tick()
        assert harness.published_hwnds == {100, 200}

        failed = []

        async def _fails_once(_clients):
            if not failed:
                failed.append(True)
                raise MemoryReadError("the dying client's last read")

        harness._adopt_by_identity = AsyncMock(side_effect=_fails_once)
        harness._handler.clients.remove(dying)
        await _tick()

        assert harness.published_hwnds == {100}
        harness._on_clients_removed.assert_called_once_with({200})
        harness._game_view.unembed_all.assert_not_called()
    finally:
        harness.stop()


async def test_stop_cancels_in_flight_activation(harness):
    """A never-resolving activation must not outlive the manager."""
    stuck = FakeHookClient(100)
    harness._handler.add(stuck)
    harness.start()
    await _tick(2)
    assert harness._activation_tasks

    tasks = list(harness._activation_tasks)
    harness.stop()
    await asyncio.sleep(0)
    assert all(t.cancelled() or t.done() for t in tasks)
    assert harness._activating_hwnds == set()


async def test_has_client_titled(harness, monkeypatch):
    """The completion predicate auto-select and recovery both wait on."""
    monkeypatch.setattr(
        "bartleby.runtime.clients.client_manager.get_window_title",
        lambda hwnd: {100: "[Alpha] Wizard101"}.get(hwnd, ""),
    )
    harness._clients = [FakeHookClient(100)]
    assert harness.has_client_titled("[Alpha] Wizard101") is True
    assert harness.has_client_titled("[Beta] Wizard101") is False


async def test_a_window_a_launch_is_logging_in_is_not_hooked_until_its_login_is_sent(harness):
    """The login borrows the root window hook's site, so the hooks must wait for it."""
    gate.hold([100])
    try:
        client = FakeHookClient(100)
        harness._handler.add(client)
        harness.start()
        await _tick()
        assert client.activate_calls == 0
        assert harness.published_hwnds == set()

        gate.release([100])
        client.hooks_ready.set_result(None)
        await _tick()
        assert client.activate_calls == 1
        assert harness.published_hwnds == {100}
    finally:
        gate.release([100])
        harness.stop()


async def test_every_new_window_waits_while_a_launch_is_spawning(harness):
    """Before the launcher has claimed its window it cannot name it, so none is hooked."""
    other = FakeHookClient(200)
    harness._handler.add(other)
    harness.start()
    try:
        with gate.spawning():
            await _tick()
            assert other.activate_calls == 0
        await _tick()
        assert other.activate_calls == 1
    finally:
        harness.stop()


async def test_a_held_window_that_closes_is_dropped(harness):
    gate.hold([100])
    try:
        client = FakeHookClient(100)
        harness._handler.add(client)
        harness.start()
        await _tick()
        harness._handler.clients.remove(client)
        await _tick()
        assert harness._gated_clients == []
        gate.release([100])
        await _tick()
        assert client.activate_calls == 0
    finally:
        gate.release([100])
        harness.stop()


async def test_a_trace_places_a_client_s_hooking_and_its_publishing(harness, tmp_path):
    """Whether a wizard is late to the list because it hooked late, or because the loop published it late."""
    client = FakeHookClient(100)
    harness._handler.add(client)
    path = run_trace.start(lambda c: None, tmp_path)
    harness.start()
    try:
        await _tick(2)
        client.hooks_ready.set_result(None)
        await _tick(3)
    finally:
        harness.stop()
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    (hook,) = [r for r in records if r.get("name") == "client.hook"]
    (published,) = [r for r in records if r.get("name") == "client.published"]
    assert hook["kind"] == "span" and hook["attrs"] == {"hwnd": 100}
    assert published["kind"] == "mark" and published["hwnd"] == 100
    assert published["t"] >= hook["t"] + hook["dur"]


# ---------------------------------------------------------------------------
# Identity: a titled client is published without waiting for its user id
# ---------------------------------------------------------------------------

_KEY = b"\x01" * 32
_UID = 196751008724043973


@pytest.fixture
def _fixed_key(monkeypatch):
    """Pin the HMAC key so fingerprints are predictable and no keyring is touched."""
    monkeypatch.setattr(account_identity, "account_key", lambda: _KEY)


def _reports(client: FakeHookClient, user_id: int, *, until: asyncio.Event | None = None) -> FakeHookClient:
    """Give *client* a user id to report, once *until* is set if given: a first read's pattern scan."""

    async def user_id_read():
        if until is not None:
            await until.wait()
        return user_id

    client.game_client = MagicMock()
    client.game_client.user_id = AsyncMock(side_effect=user_id_read)
    return client


def _learned(harness: LoopHarness, username: str) -> bool:
    stored = account_identity.stored_fingerprint(harness._settings.accounts, username)
    return stored == account_identity.fingerprint(_UID, _KEY)


async def test_a_titled_client_is_published_while_its_identity_is_still_being_read(harness, _fixed_key):
    """The startup trace's 12 s: each titled client's first read held every client's publishing back."""
    read = asyncio.Event()
    client = _reports(FakeHookClient(100), _UID, until=read)
    harness._handler.add(client)
    harness._hwnd_account_map[100] = "alpha"
    harness._settings.accounts = [{"username": "alpha"}]
    harness.start()
    try:
        client.hooks_ready.set_result(None)
        await _tick(4)
        assert harness.published_hwnds == {100}
        assert client.game_client.user_id.await_count == 1, "begun once, and not again each pass"
        assert not _learned(harness, "alpha")

        read.set()
        await _tick(2)
        assert _learned(harness, "alpha")
    finally:
        read.set()
        harness.stop()


async def test_an_untitled_client_is_placed_by_its_identity_before_it_is_published(harness, _fixed_key):
    """Adopting still comes first: the slot it is given depends on which account it is."""
    client = _reports(FakeHookClient(100), _UID)
    harness._handler.add(client)
    harness._settings.accounts = [
        {
            "username": "alpha",
            "uid_fingerprint": account_identity.fingerprint(_UID, _KEY),
            "uid_scheme": account_identity.SCHEME,
        }
    ]
    published: list[tuple[dict, list]] = []
    harness.clients_changed.emit.side_effect = lambda clients: (
        published.append((dict(harness._hwnd_account_map), list(harness._client_order))) if clients else None
    )
    harness.start()
    try:
        client.hooks_ready.set_result(None)
        await _tick(3)
        assert published[0] == ({100: "alpha"}, ["alpha"])
    finally:
        harness.stop()


async def test_stop_cancels_a_read_learning_an_identity(harness, _fixed_key):
    """A first read can take seconds; it must not outlive the manager."""
    read = asyncio.Event()
    client = _reports(FakeHookClient(100), _UID, until=read)
    harness._handler.add(client)
    harness._hwnd_account_map[100] = "alpha"
    harness._settings.accounts = [{"username": "alpha"}]
    harness.start()
    client.hooks_ready.set_result(None)
    await _tick(3)
    tasks = list(harness._identity_tasks.values())
    assert len(tasks) == 1

    harness.stop()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert all(t.cancelled() for t in tasks)
    assert harness._identity_tasks == {}
    assert not _learned(harness, "alpha")


async def test_a_trace_shows_an_identity_learned_apart_from_the_pass(harness, _fixed_key, tmp_path):
    """Its own span, on no pass's, and only for the read: a pass that has the id does without one."""
    client = _reports(FakeHookClient(100), _UID)
    harness._handler.add(client)
    harness._hwnd_account_map[100] = "alpha"
    harness._settings.accounts = [{"username": "alpha"}]
    path = run_trace.start(lambda c: None, tmp_path)
    harness.start()
    try:
        client.hooks_ready.set_result(None)
        await _tick(5)
    finally:
        harness.stop()
        run_trace.stop()

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    (learn,) = [r for r in records if r.get("name") == "client.identity_learn"]
    assert learn["kind"] == "span" and learn["attrs"] == {"hwnd": 100}
    assert learn["parent"] is None
