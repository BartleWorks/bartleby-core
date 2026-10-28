"""The arbiter: every input to a client goes through one owner at a time.

In BartleBot one FSM owns every client. In BartleBuddy several behaviors run side by side
on each client -- following, auto-combat, auto-group, the hotkey helpers -- so ownership is
per client and per moment, and this is where it is decided.

A behavior that wants to press anything takes a lease::

    async with arbiter.acquire(client, Owner.MOVEMENT) as lease:
        await lease.input.hold(GameAction.MOVE_FORWARD)

The lease is the only place a :class:`~bartleby.runtime.input.ClientInput` comes from, so
with no lease there is no input API to call. It is also the one place that knows who sent
each key, which is what the GUI shows as "who holds each client".

**Priorities preempt** (:class:`Owner`, highest last): a higher claim cancels the holder's
task where it awaits, and the holder's ``async with`` raises :class:`Preempted` instead of
``CancelledError``, so a behavior catches it and resumes from its own state once the client
is free again (:meth:`Arbiter.wait_free`). A claim equal or lower waits its turn; with
``wait=False`` it raises :class:`Busy` instead. Whatever ends a lease lets go of every key it
held. :meth:`Arbiter.revoke_all` is Stop all: every lease ends at once, with :class:`Revoked`.

Memory reads take no lease.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import IntEnum

from bartleby.runtime.input import ClientInput

logger = logging.getLogger(__name__)


class Owner(IntEnum):
    """Who holds a client, lowest priority first."""

    QUESTER = 10
    MOVEMENT = 20
    HOTKEY = 30
    COMBAT = 40
    RECOVERY = 50

    @property
    def label(self) -> str:
        return _LABELS[self]


_LABELS = {
    Owner.QUESTER: "questing",
    Owner.MOVEMENT: "moving",
    Owner.HOTKEY: "hotkey",
    Owner.COMBAT: "in combat",
    Owner.RECOVERY: "recovering",
}


class LeaseLost(Exception):
    """The lease ended before its holder let it go."""


class Preempted(LeaseLost):
    """A higher-priority owner took the client."""

    def __init__(self, by: Owner) -> None:
        super().__init__(f"preempted by {by.name}")
        self.by = by


class Revoked(LeaseLost):
    """Stop all, or the client went away."""


class Busy(LeaseLost):
    """The client is held by an owner this claim cannot preempt, and the claim would not wait."""


def client_key(client) -> int:
    return client.window_handle


@dataclass(eq=False)
class Lease:
    """One owner's hold on one client. Its ``input`` works only while it is active."""

    client: object
    owner: Owner
    input: ClientInput
    detail: str = ""
    active: bool = True
    lost: LeaseLost | None = None


@dataclass(eq=False)
class _Slot:
    lease: Lease | None = None
    task: asyncio.Task | None = None
    waiters: list[tuple[int, int, asyncio.Future]] = field(default_factory=list)
    freed: asyncio.Event = field(default_factory=asyncio.Event)


#: Called with the client's key and its new holder (None when free) whenever that changes.
HolderListener = Callable[[int, Lease | None], None]


class Arbiter:
    """Leases on clients, by priority. One per runtime, used from its event loop only."""

    def __init__(self, make_input: Callable[[object], ClientInput] = ClientInput) -> None:
        self._make_input = make_input
        self._slots: dict[int, _Slot] = {}
        self._listeners: list[HolderListener] = []
        self._order = itertools.count()

    # -- reading ----------------------------------------------------------------

    def holder(self, client) -> Lease | None:
        slot = self._slots.get(client_key(client))
        return slot.lease if slot else None

    def subscribe(self, listener: HolderListener) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def _notify(self, key: int, lease: Lease | None) -> None:
        for listener in list(self._listeners):
            try:
                listener(key, lease)
            except Exception:
                logger.exception("Arbiter listener raised")

    # -- taking -------------------------------------------------------------------

    def acquire(self, client, owner: Owner, *, wait: bool = True, detail: str = "") -> _Acquire:
        """A lease on *client* for *owner*, as an async context manager."""
        return _Acquire(self, client, owner, wait, detail)

    async def wait_free(self, client, below: Owner | None = None) -> None:
        """Return once *client* is free, or held only by an owner below *below*."""
        slot = self._slot(client_key(client))
        while slot.lease is not None and (below is None or slot.lease.owner >= below):
            slot.freed.clear()
            await slot.freed.wait()

    def _slot(self, key: int) -> _Slot:
        return self._slots.setdefault(key, _Slot())

    async def _take(self, client, owner: Owner, wait: bool, detail: str) -> Lease:
        key = client_key(client)
        slot = self._slot(key)
        task = asyncio.current_task()
        if slot.lease is not None and slot.task is task:
            raise RuntimeError(f"{owner.name} already holds this client from the same task")
        while True:
            if slot.lease is None and not self._outranked(slot, owner):
                lease = Lease(client, owner, self._make_input(client), detail)
                slot.lease, slot.task = lease, task
                self._notify(key, lease)
                return lease
            if slot.lease is not None and owner > slot.lease.owner and slot.lease.lost is None:
                self._end(slot, Preempted(owner))
            elif not wait:
                raise Busy(f"held by {slot.lease.owner.name}" if slot.lease else "claimed by a higher owner")
            future = asyncio.get_running_loop().create_future()
            entry = (-int(owner), next(self._order), future)
            slot.waiters.append(entry)
            try:
                await future
            finally:
                slot.waiters.remove(entry)

    def _outranked(self, slot: _Slot, owner: Owner) -> bool:
        """Whether a waiter of a higher priority than *owner* is due the client first."""
        return any(-priority > owner for priority, _, _ in slot.waiters)

    def _end(self, slot: _Slot, why: LeaseLost) -> None:
        """End the lease from outside its task: cancel the task where it awaits."""
        lease = slot.lease
        if lease is None or lease.lost is not None:
            return
        lease.lost = why
        if slot.task is not None and not slot.task.done():
            slot.task.cancel()

    async def _give_back(self, lease: Lease) -> None:
        key = client_key(lease.client)
        slot = self._slot(key)
        lease.active = False
        await lease.input.close()
        if slot.lease is lease:
            slot.lease, slot.task = None, None
            slot.freed.set()
            self._notify(key, None)
            self._wake_next(slot)

    def _wake_next(self, slot: _Slot) -> None:
        for _, _, future in sorted(slot.waiters):
            if not future.done():
                future.set_result(None)
                return

    # -- ending ---------------------------------------------------------------------

    async def revoke(self, client) -> None:
        """End *client*'s lease, whoever holds it, and let go of its keys now."""
        slot = self._slots.get(client_key(client))
        if slot is None or slot.lease is None:
            return
        lease = slot.lease
        self._end(slot, Revoked("revoked"))
        # The holder lets go as it unwinds; this makes sure of it even if it is stuck.
        await lease.input.release_all()

    async def revoke_all(self) -> None:
        """Stop all: end every lease, and let go of every held key."""
        for slot in list(self._slots.values()):
            if slot.lease is not None:
                await self.revoke(slot.lease.client)

    def forget(self, client) -> None:
        """Drop a client that went away. Its lease, if any, has been revoked."""
        self._slots.pop(client_key(client), None)


class _Acquire:
    def __init__(self, arbiter: Arbiter, client, owner: Owner, wait: bool, detail: str) -> None:
        self._arbiter, self._client, self._owner, self._wait, self._detail = arbiter, client, owner, wait, detail
        self._lease: Lease | None = None

    async def __aenter__(self) -> Lease:
        self._lease = await self._arbiter._take(self._client, self._owner, self._wait, self._detail)
        return self._lease

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        lease = self._lease
        assert lease is not None
        lost = lease.lost
        convert = False
        task = asyncio.current_task()
        if lost is not None and task is not None and task.cancelling():
            # Take back the arbiter's own cancel, delivered or still pending (a body that
            # finished first). Only that one becomes LeaseLost: any other cancel (the
            # behavior being stopped) stays a CancelledError.
            convert = task.uncancel() == 0 and exc_type is asyncio.CancelledError
        with contextlib.suppress(Exception):
            await self._arbiter._give_back(lease)
        if convert:
            raise lost from None
        return False
