"""Tests for ClientManager's identity pass: learning and adopting accounts by user id.

The title path can only place a client BartleBot launched. Adopting places the rest, by
the account-level user id the client reports in memory, so a window launched elsewhere
and adopted into BartleBot can still be recovered when it crashes. Learning writes a
titled client's fingerprint to its account, in a task of its own, so its read never holds
up the pass (``_learn_identities``).

Exercises the real methods against a stub holding only the attributes they touch.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bartleby.runtime.clients import account_identity
from bartleby.runtime.clients.client_manager import ClientManager

KEY = b"\x01" * 32
UID_ONE = 196751008724043973
UID_TWO = 196751008724043974


@pytest.fixture(autouse=True)
def _fixed_key(monkeypatch):
    """Pin the HMAC key so fingerprints are predictable and no keyring is touched."""
    monkeypatch.setattr(account_identity, "account_key", lambda: KEY)


def _fp(user_id: int) -> str:
    return account_identity.fingerprint(user_id, KEY)


def _client(hwnd: int, user_id: int | None, *, until: asyncio.Event | None = None):
    """A client whose game_client reports *user_id*, or raises if None; once *until* is set, if given."""
    client = MagicMock(window_handle=hwnd)

    async def user_id_read():
        if until is not None:
            await until.wait()
        if user_id is None:
            raise RuntimeError("client is dying")
        return user_id

    client.game_client.user_id = AsyncMock(side_effect=user_id_read)
    return client


class IdentityHarness:
    """The attributes the identity pass uses on self."""

    _learn_identities = ClientManager._learn_identities
    _identity_task_done = ClientManager._identity_task_done
    _learn_identity = ClientManager._learn_identity
    _learn_fingerprint = ClientManager._learn_fingerprint
    _adopt_by_identity = ClientManager._adopt_by_identity
    _read_user_id = ClientManager._read_user_id
    _assign_slot = ClientManager._assign_slot

    def __init__(self, accounts: list[dict]) -> None:
        self._hwnd_account_map: dict[int, str] = {}
        self._hwnd_user_ids: dict[int, int] = {}
        self._identity_tasks: dict[int, asyncio.Task] = {}
        self._client_order: list[str | None] = []
        self._vacated_slots: dict[str, int] = {}
        self.settings = MagicMock()
        self.settings.accounts = accounts
        self._settings = self.settings


async def _run(harness: IdentityHarness, clients: list) -> None:
    """One pass's identity step, as the loop runs it, and then the learning it started, to its end.

    An async helper rather than asyncio.run(): the suite runs under asyncio_mode="auto",
    and opening a second loop inside a sync test leaves a closed loop behind that breaks
    unrelated tests later in the session.
    """
    harness._learn_identities(clients)
    await harness._adopt_by_identity(clients)
    await asyncio.gather(*harness._identity_tasks.values())


def _accounts(*entries: tuple[str, int | None]) -> list[dict]:
    """Build account dicts; a None user id means the account has no fingerprint yet."""
    out = []
    for username, user_id in entries:
        entry = {"username": username, "alias": username.title()}
        if user_id is not None:
            entry["uid_fingerprint"] = _fp(user_id)
            entry["uid_scheme"] = account_identity.SCHEME
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Adopting
# ---------------------------------------------------------------------------


async def test_adopts_a_client_whose_fingerprint_is_known():
    """The whole point: a window BartleBot never launched becomes recoverable."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    await _run(harness, [_client(100, UID_ONE)])

    assert harness._hwnd_account_map == {100: "user1"}
    assert harness._client_order == ["user1"], "an adopted client takes a slot"


async def test_ignores_a_client_whose_fingerprint_is_unknown():
    """An account never launched from here has nothing on file — the known limitation."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    await _run(harness, [_client(100, UID_TWO)])

    assert harness._hwnd_account_map == {}
    assert harness._client_order == []


async def test_will_not_adopt_an_account_a_live_client_already_answers_for(caplog):
    """Two windows cannot be one account; guessing would relaunch the wrong one."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    harness._hwnd_account_map = {100: "user1"}

    with caplog.at_level("WARNING", logger="bartleby.runtime.clients.client_manager"):
        await _run(harness, [_client(100, UID_ONE), _client(200, UID_ONE)])

    assert 200 not in harness._hwnd_account_map
    assert any("already answering" in r.getMessage() for r in caplog.records)


async def test_a_client_that_has_not_logged_in_is_left_alone():
    """user_id reads 0 until login completes."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    await _run(harness, [_client(100, 0)])

    assert harness._hwnd_account_map == {}
    assert harness._hwnd_user_ids == {}, "zero must not be cached, or it never resolves"


async def test_a_failing_read_does_not_raise():
    """This runs in the client loop, where an exception would cost the rest of the pass."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    await _run(harness, [_client(100, None)])

    assert harness._hwnd_account_map == {}


async def test_no_configured_accounts_reads_nothing():
    harness = IdentityHarness([])
    client = _client(100, UID_ONE)
    await _run(harness, [client])

    client.game_client.user_id.assert_not_awaited()


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------


async def test_learns_the_fingerprint_of_a_launched_client():
    """The title placed it, so the account is certain — this is the only moment we can learn."""
    harness = IdentityHarness(_accounts(("user1", None)))
    harness._hwnd_account_map = {100: "user1"}

    await _run(harness, [_client(100, UID_ONE)])

    written = harness.settings.accounts
    assert account_identity.stored_fingerprint(written, "user1") == _fp(UID_ONE)


async def test_learning_replaces_a_stale_fingerprint():
    """Launch is authoritative: we typed the credentials, so what it reports is the truth."""
    harness = IdentityHarness(_accounts(("user1", UID_TWO)))
    harness._hwnd_account_map = {100: "user1"}

    await _run(harness, [_client(100, UID_ONE)])

    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") == _fp(UID_ONE)


async def test_an_unchanged_fingerprint_is_not_rewritten():
    """Settings are written to disk; a write per second would be absurd."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    harness._hwnd_account_map = {100: "user1"}
    accounts_before = harness.settings.accounts

    await _run(harness, [_client(100, UID_ONE)])

    assert harness.settings.accounts is accounts_before, "no setter call means no disk write"


async def test_adopted_client_is_not_relearned_on_the_next_pass():
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    client = _client(100, UID_ONE)

    await _run(harness, [client])
    accounts_after_adopt = harness.settings.accounts
    await _run(harness, [client])

    assert harness.settings.accounts is accounts_after_adopt
    assert harness._hwnd_account_map == {100: "user1"}


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


async def test_user_id_is_read_once_per_window():
    """The first read resolves GameClient's base by pattern scan — far too costly per tick."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    client = _client(100, UID_ONE)

    await _run(harness, [client])
    await _run(harness, [client])
    await _run(harness, [client])

    assert client.game_client.user_id.await_count == 1
    assert harness._hwnd_user_ids == {100: UID_ONE}


async def test_a_pending_login_is_retried_until_it_resolves():
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    client = MagicMock(window_handle=100)
    client.game_client.user_id = AsyncMock(side_effect=[0, 0, UID_ONE])

    await _run(harness, [client])
    await _run(harness, [client])
    assert harness._hwnd_account_map == {}

    await _run(harness, [client])
    assert harness._hwnd_account_map == {100: "user1"}


# ---------------------------------------------------------------------------
# Learning off the pass
# ---------------------------------------------------------------------------


async def test_learning_a_titled_client_does_not_hold_up_the_pass():
    """Its first read is a pattern scan of seconds, and nothing the pass does needs its answer."""
    harness = IdentityHarness(_accounts(("user1", None)))
    harness._hwnd_account_map = {100: "user1"}
    read = asyncio.Event()
    client = _client(100, UID_ONE, until=read)

    harness._learn_identities([client])
    await harness._adopt_by_identity([client])
    await asyncio.sleep(0)

    assert client.game_client.user_id.await_count == 1, "the read has begun"
    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") is None
    (task,) = harness._identity_tasks.values()

    read.set()
    await task
    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") == _fp(UID_ONE)
    assert harness._identity_tasks == {}, "a finished read is forgotten"


async def test_learning_is_not_started_again_while_a_read_runs():
    """The loop passes once a second, and a first read can take longer than that."""
    harness = IdentityHarness(_accounts(("user1", None)))
    harness._hwnd_account_map = {100: "user1"}
    read = asyncio.Event()
    client = _client(100, UID_ONE, until=read)

    for _ in range(3):
        harness._learn_identities([client])
        await asyncio.sleep(0)

    assert len(harness._identity_tasks) == 1
    assert client.game_client.user_id.await_count == 1
    read.set()
    await asyncio.gather(*harness._identity_tasks.values())


async def test_a_window_whose_id_is_read_already_is_learned_without_a_task():
    """No read is left to wait on, so a pass checks the fingerprint inline, as it always did."""
    harness = IdentityHarness(_accounts(("user1", None)))
    harness._hwnd_account_map = {100: "user1"}
    harness._hwnd_user_ids = {100: UID_ONE}

    harness._learn_identities([_client(100, UID_ONE)])

    assert harness._identity_tasks == {}
    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") == _fp(UID_ONE)


async def test_a_learning_read_of_zero_is_tried_again_on_a_later_pass():
    harness = IdentityHarness(_accounts(("user1", None)))
    harness._hwnd_account_map = {100: "user1"}
    client = MagicMock(window_handle=100)
    client.game_client.user_id = AsyncMock(side_effect=[0, UID_ONE])

    await _run(harness, [client])
    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") is None

    await _run(harness, [client])
    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") == _fp(UID_ONE)


async def test_a_window_that_loses_its_account_mid_read_teaches_nothing():
    """Unhooked or closed while the read ran: the id may no longer be that account's window."""
    harness = IdentityHarness(_accounts(("user1", None)))
    harness._hwnd_account_map = {100: "user1"}
    read = asyncio.Event()

    harness._learn_identities([_client(100, UID_ONE, until=read)])
    await asyncio.sleep(0)
    harness._hwnd_account_map.clear()
    read.set()
    await asyncio.gather(*harness._identity_tasks.values())

    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") is None


async def test_an_account_removed_from_the_settings_is_not_written_every_pass():
    """Its window keeps the title's account until it closes, but there is no record to learn into."""
    harness = IdentityHarness(_accounts(("user2", None)))
    harness._hwnd_account_map = {100: "user1"}
    accounts_before = harness.settings.accounts

    await _run(harness, [_client(100, UID_ONE)])
    await _run(harness, [_client(100, UID_ONE)])

    assert harness.settings.accounts is accounts_before


async def test_a_learning_read_that_fails_does_not_raise():
    harness = IdentityHarness(_accounts(("user1", None)))
    harness._hwnd_account_map = {100: "user1"}

    await _run(harness, [_client(100, None)])

    assert account_identity.stored_fingerprint(harness.settings.accounts, "user1") is None


# ---------------------------------------------------------------------------
# Adopting several at once
# ---------------------------------------------------------------------------


async def test_untitled_clients_are_read_together():
    """One window's slow first read must not be paid again by each window after it."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE), ("user2", UID_TWO)))
    started = 0
    both = asyncio.Event()

    def _reports(hwnd, user_id):
        async def user_id_read():
            nonlocal started
            started += 1
            if started == 2:
                both.set()
            await both.wait()
            return user_id

        client = MagicMock(window_handle=hwnd)
        client.game_client.user_id = AsyncMock(side_effect=user_id_read)
        return client

    # The ceiling only turns a read that waits on the other, one after another, into a failure.
    await asyncio.wait_for(_run(harness, [_reports(100, UID_ONE), _reports(200, UID_TWO)]), 5.0)

    assert harness._hwnd_account_map == {100: "user1", 200: "user2"}


async def test_of_two_windows_reporting_one_account_the_first_listed_is_adopted(caplog):
    """Read together, but decided in the list's order, whichever read came back first."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    first_read = asyncio.Event()
    late = _client(100, UID_ONE, until=first_read)

    async def quick_read():
        first_read.set()
        return UID_ONE

    quick = MagicMock(window_handle=200)
    quick.game_client.user_id = AsyncMock(side_effect=quick_read)

    with caplog.at_level("WARNING", logger="bartleby.runtime.clients.client_manager"):
        await _run(harness, [late, quick])

    assert harness._hwnd_account_map == {100: "user1"}
    assert harness._client_order == ["user1"]
    assert any("already answering" in r.getMessage() for r in caplog.records)


async def test_a_window_still_being_learned_is_not_read_again_for_adopting():
    """Its base would be resolved twice at once; the adoption waits for a later pass."""
    harness = IdentityHarness(_accounts(("user1", UID_ONE)))
    client = _client(100, UID_ONE)
    harness._identity_tasks[100] = MagicMock()

    await harness._adopt_by_identity([client])

    client.game_client.user_id.assert_not_awaited()
    assert harness._hwnd_account_map == {}
