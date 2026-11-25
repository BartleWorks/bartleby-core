"""Input: keys by the account's binding, held keys repeated, and speed capped at 2.0x."""

import asyncio
import time

import pytest

from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.input import SPEED_VALUE_MAX, ClientInput, held_key_watchdog, speed_value
from bartleby.runtime.testing.sim_client import SimClient, SimClock


@pytest.mark.parametrize(("multiplier", "value"), [(1.0, 0), (1.4, 40), (2.0, 100), (2.5, 100), (0.5, 0)])
def test_the_speed_field_is_the_whole_bonus_and_never_above_2x(multiplier, value):
    assert speed_value(multiplier) == value


async def test_a_speed_write_above_the_cap_is_clamped():
    client = SimClient()
    await ClientInput(client).write_speed(250)
    assert client.speed == SPEED_VALUE_MAX


async def test_a_held_key_is_repeated_until_released(monkeypatch):
    monkeypatch.setattr("bartleby.runtime.input.HELD_KEY_REPEAT", 0.005)
    sent = []

    class Sender:
        async def down(self, code):
            sent.append(code)

        async def up(self, code):
            sent.append(-code)

    keys = ClientInput(SimClient(), Sender())
    assert await keys.hold(GameAction.MOVE_FORWARD)
    assert not await keys.hold(GameAction.MOVE_FORWARD)
    await asyncio.sleep(0.05)
    await keys.release_all()
    w = ord("W")
    assert sent.count(w) > 2
    assert sent[-1] == -w


async def test_the_simulated_wizard_walks_while_forward_is_held():
    clock = SimClock()
    client = SimClient(clock=clock)
    keys = ClientInput(client)
    await keys.write_yaw(0.0)  # faces -y
    await keys.hold(GameAction.MOVE_FORWARD)
    clock.advance(1.0)
    await keys.release_all()
    x, y, _ = client.position
    assert x == pytest.approx(0.0, abs=1e-6)
    assert y == pytest.approx(-600.0)


async def test_the_simulated_wizard_walks_twice_as_fast_at_the_cap():
    clock = SimClock()
    client = SimClient(clock=clock, speed=100)
    keys = ClientInput(client)
    await keys.hold(GameAction.MOVE_FORWARD)
    clock.advance(0.5)
    await keys.release_all()
    assert client.position[1] == pytest.approx(-600.0)


async def test_a_cancel_that_lands_while_letting_go_still_cancels_the_caller():
    """Letting go waits for the repeat to end, and a cancel of the caller in that wait is the caller's.

    Swallowed, it left a follower walking on after its behavior was stopped, which hung a test of
    it under the full suite's load.
    """
    sent = []

    class Sender:
        async def down(self, code):
            sent.append(code)

        async def up(self, code):
            sent.append(-code)

    keys = ClientInput(SimClient(), Sender())
    await keys.hold(GameAction.MOVE_FORWARD)
    letting_go = asyncio.create_task(keys.release_all())
    await asyncio.sleep(0)  # It has cancelled the repeat, and waits for it.

    letting_go.cancel()
    await asyncio.wait([letting_go], timeout=1)

    assert letting_go.cancelled(), "the caller's own cancel was swallowed"
    assert sent[-1] == -ord("W"), "and the key is let go all the same"
    assert keys.held == frozenset()


# --- a held event loop ------------------------------------------------------------------------


async def test_a_held_event_loop_lets_go_of_every_held_key_and_does_not_press_it_again():
    client = SimClient()
    keys = ClientInput(client)
    await keys.hold(GameAction.MOVE_FORWARD)
    await keys.hold(GameAction.MOVE_LEFT)

    time.sleep(0.5)  # the loop held, as by a long synchronous call

    assert client.held == set()
    assert keys.held == frozenset()
    assert keys.dropped == 1
    let_go = len(client.inputs)
    await asyncio.sleep(0.2)
    assert client.held == set()
    assert [r.kind for r in client.inputs[let_go:]] == []
    await keys.release_all()


async def test_a_loop_that_keeps_up_never_has_its_keys_let_go():
    client = SimClient()
    keys = ClientInput(client)
    await keys.hold(GameAction.MOVE_FORWARD)

    await asyncio.sleep(0.4)

    assert keys.held == {GameAction.MOVE_FORWARD}
    assert keys.dropped == 0
    await keys.release_all()


async def test_a_key_let_go_by_the_watchdog_is_held_again_when_its_holder_holds_it():
    client = SimClient()
    keys = ClientInput(client)
    await keys.hold(GameAction.MOVE_FORWARD)
    time.sleep(0.5)

    assert await keys.hold(GameAction.MOVE_FORWARD)

    assert client.holding(GameAction.MOVE_FORWARD)
    await keys.release_all()


async def test_the_watchdog_watches_only_while_a_key_is_held():
    keys = ClientInput(SimClient())
    await keys.hold(GameAction.MOVE_FORWARD)
    assert held_key_watchdog.watches(keys)

    await keys.release_all()
    await asyncio.sleep(0.2)

    assert not held_key_watchdog.watches(keys)
