"""Input: keys by the account's binding, held keys repeated, and speed capped at 2.0x."""

import asyncio

import pytest

from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.input import SPEED_VALUE_MAX, ClientInput, speed_value
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
