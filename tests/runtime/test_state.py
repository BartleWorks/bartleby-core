"""The state reader publishes snapshots on its cadences, and keeps the last value on a failed read."""

import asyncio

from bartleby.runtime.state import Snapshot, StateReader
from bartleby.runtime.testing.sim_client import SimClient


async def test_a_snapshot_carries_position_zone_battle_and_speed():
    client = SimClient(position=(1.0, 2.0, 3.0), zone="WizardCity/WC_Streets/Unicorn_Way", battle=True, speed=40)
    reader = StateReader(client, position_interval=0.001)
    seen: list[Snapshot] = []
    reader.subscribe(seen.append)
    reader.start()
    await asyncio.sleep(0.02)
    await reader.stop()
    latest = reader.latest
    assert latest.position == (1.0, 2.0, 3.0)
    assert latest.zone == "WizardCity/WC_Streets/Unicorn_Way"
    assert latest.in_battle and latest.speed == 40 and latest.readable
    assert len(seen) > 1


async def test_a_loading_client_has_no_zone_and_is_not_readable():
    reader = StateReader(SimClient(loading=True), position_interval=0.001)
    reader.start()
    await asyncio.sleep(0.01)
    await reader.stop()
    assert reader.latest.zone is None and not reader.latest.readable


async def test_a_failing_read_keeps_the_last_value():
    client = SimClient(position=(5.0, 5.0, 0.0))
    reader = StateReader(client, position_interval=0.001)
    reader.start()
    await asyncio.sleep(0.01)

    async def broken():
        raise RuntimeError("mid zone change")

    client.body.position = broken
    await asyncio.sleep(0.01)
    await reader.stop()
    assert reader.latest.position == (5.0, 5.0, 0.0)


def test_a_listener_that_raises_does_not_stop_the_others():
    reader = StateReader(SimClient())
    heard = []

    def bad(_):
        raise ValueError

    reader.subscribe(bad)
    reader.subscribe(heard.append)
    reader.publish(zone="X")
    assert heard and heard[0].zone == "X"
