"""The arbiter: one owner per client, priorities preempt, and a lost lease lets go of its keys."""

import asyncio

import pytest

from bartleby.runtime.arbiter import Arbiter, Busy, Owner, Preempted, Revoked
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.input import InputClosed
from bartleby.runtime.testing.sim_client import SimClient


@pytest.fixture
def arbiter():
    return Arbiter()


@pytest.fixture
def client():
    return SimClient()


async def test_a_lease_hands_out_input_and_names_its_owner(arbiter, client):
    async with arbiter.acquire(client, Owner.HOTKEY) as lease:
        assert arbiter.holder(client) is lease
        await lease.input.press(GameAction.INTERACT)
    assert arbiter.holder(client) is None
    assert client.presses(GameAction.INTERACT) == 1


async def test_input_kept_past_the_lease_cannot_press(arbiter, client):
    async with arbiter.acquire(client, Owner.HOTKEY) as lease:
        kept = lease.input
    with pytest.raises(InputClosed):
        await kept.press(GameAction.INTERACT)


async def test_ending_a_lease_lets_go_of_every_held_key(arbiter, client):
    async with arbiter.acquire(client, Owner.MOVEMENT) as lease:
        await lease.input.hold(GameAction.MOVE_FORWARD)
        assert client.holding(GameAction.MOVE_FORWARD)
    assert not client.held


async def test_a_higher_owner_preempts_and_the_holder_sees_preempted(arbiter, client):
    holding = asyncio.Event()
    seen = []

    async def mover():
        try:
            async with arbiter.acquire(client, Owner.MOVEMENT) as lease:
                await lease.input.hold(GameAction.MOVE_FORWARD)
                holding.set()
                await asyncio.sleep(10)
        except Preempted as e:
            seen.append(e.by)

    task = asyncio.create_task(mover())
    await holding.wait()
    async with arbiter.acquire(client, Owner.COMBAT) as lease:
        assert lease.owner is Owner.COMBAT
        assert not client.held, "the held key is let go before combat takes over"
    await task
    assert seen == [Owner.COMBAT]
    assert not task.cancelled()


async def test_a_preempt_landing_after_the_body_finished_is_absorbed(arbiter, client):
    async def mover():
        async with arbiter.acquire(client, Owner.MOVEMENT):
            arbiter._end(arbiter._slots[client.window_handle], Preempted(Owner.COMBAT))
        await asyncio.sleep(0)
        return "went on"

    assert await mover() == "went on"


async def test_an_equal_or_lower_claim_waits_its_turn(arbiter, client):
    order = []
    release = asyncio.Event()

    async def first():
        async with arbiter.acquire(client, Owner.HOTKEY):
            order.append("hotkey in")
            await release.wait()
            order.append("hotkey out")

    async def second():
        async with arbiter.acquire(client, Owner.MOVEMENT):
            order.append("movement in")

    a = asyncio.create_task(first())
    await asyncio.sleep(0)
    b = asyncio.create_task(second())
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(a, b)
    assert order == ["hotkey in", "hotkey out", "movement in"]


async def test_a_claim_that_will_not_wait_is_refused(arbiter, client):
    async with arbiter.acquire(client, Owner.HOTKEY):
        other = asyncio.create_task(_take_without_waiting(arbiter, client))
        with pytest.raises(Busy):
            await other


async def _take_without_waiting(arbiter, client):
    async with arbiter.acquire(client, Owner.MOVEMENT, wait=False):
        pass


async def test_a_waiting_higher_claim_goes_before_a_waiting_lower_one(arbiter, client):
    order = []
    release = asyncio.Event()

    async def hold():
        async with arbiter.acquire(client, Owner.RECOVERY):
            await release.wait()

    async def claim(owner):
        async with arbiter.acquire(client, owner):
            order.append(owner)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0)
    low = asyncio.create_task(claim(Owner.QUESTER))
    await asyncio.sleep(0)
    high = asyncio.create_task(claim(Owner.COMBAT))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(holder, low, high)
    assert order == [Owner.COMBAT, Owner.QUESTER]


async def test_stop_all_revokes_every_lease_and_lets_go_of_keys(arbiter):
    clients = [SimClient(), SimClient()]
    ready = asyncio.Barrier(3)
    outcomes = []

    async def walk(client):
        try:
            async with arbiter.acquire(client, Owner.MOVEMENT) as lease:
                await lease.input.hold(GameAction.MOVE_FORWARD)
                await ready.wait()
                await asyncio.sleep(10)
        except Revoked:
            outcomes.append("revoked")

    tasks = [asyncio.create_task(walk(c)) for c in clients]
    await ready.wait()
    await arbiter.revoke_all()
    await asyncio.gather(*tasks)
    assert outcomes == ["revoked", "revoked"]
    assert all(not c.held for c in clients)
    assert all(arbiter.holder(c) is None for c in clients)


async def test_a_behavior_being_stopped_stays_cancelled_even_when_revoked_too(arbiter, client):
    inside = asyncio.Event()

    async def mover():
        async with arbiter.acquire(client, Owner.MOVEMENT):
            inside.set()
            await asyncio.sleep(10)

    task = asyncio.create_task(mover())
    await inside.wait()
    task.cancel()
    await arbiter.revoke(client)
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_wait_free_returns_once_the_holder_lets_go(arbiter, client):
    release = asyncio.Event()

    async def hold():
        async with arbiter.acquire(client, Owner.HOTKEY):
            await release.wait()

    task = asyncio.create_task(hold())
    await asyncio.sleep(0)
    waiter = asyncio.create_task(arbiter.wait_free(client))
    await asyncio.sleep(0.01)
    assert not waiter.done()
    release.set()
    await asyncio.wait_for(waiter, 1)
    await task


async def test_listeners_hear_every_change_of_holder(arbiter, client):
    heard = []
    arbiter.subscribe(lambda key, lease: heard.append(lease.owner if lease else None))
    async with arbiter.acquire(client, Owner.HOTKEY):
        pass
    assert heard == [Owner.HOTKEY, None]


async def test_the_same_task_cannot_take_a_client_twice(arbiter, client):
    async with arbiter.acquire(client, Owner.MOVEMENT):
        with pytest.raises(RuntimeError):
            async with arbiter.acquire(client, Owner.HOTKEY):
                pass
