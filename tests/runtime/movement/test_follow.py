"""Following: the plan from snapshots, and a follower chasing a leader on simulated clients."""

import asyncio
import math

import pytest

from bartleby.runtime.arbiter import Arbiter, Lease, Owner
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.input import ClientInput
from bartleby.runtime.movement import follow
from bartleby.runtime.movement.follow import Follower, StepKind, spacing_for
from bartleby.runtime.movement.geometry import flat_distance
from bartleby.runtime.movement.trail import Trail
from bartleby.runtime.state import Snapshot, StateReader
from bartleby.runtime.testing.sim_client import SimClient

ZONE = "WizardCity/WC_Streets/Unicorn_Way"


def snap(x, y, t=0.0, zone=ZONE, **kw):
    return Snapshot(key=1, time=t, position=(x, y, 0.0), yaw=0.0, zone=zone, **kw)


def follower(trail=None, index=0):
    client = SimClient()
    leader = SimClient()
    return Follower(
        client,
        StateReader(client),
        StateReader(leader),
        trail or Trail(),
        Arbiter(),
        spacing=spacing_for(index),
    )


def laid_trail(*xs, zone=ZONE):
    trail = Trail()
    for i, x in enumerate(xs):
        trail.note(snap(x, 0, t=i * 0.1, zone=zone))
    return trail


def test_nothing_readable_is_a_wait():
    f = follower()
    assert f.plan(snap(0, 0), None, snap(500, 0), 0).kind is StepKind.WAIT
    assert f.plan(snap(0, 0, loading=True), (0, 0, 0), snap(500, 0), 0).kind is StepKind.WAIT


def test_a_follower_in_a_fight_leaves_it_to_combat():
    f = follower()
    assert f.plan(snap(0, 0, in_battle=True), (0, 0, 0), snap(900, 0), 0).kind is StepKind.WAIT


def test_it_walks_the_next_point_of_the_trail_not_straight_at_the_leader():
    trail = laid_trail(0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600)
    f = follower(trail)
    step = f.plan(snap(0, 0), (0, 0, 0), snap(600, 0), 0)
    assert step.kind is StepKind.WALK
    assert step.target[0] < 200
    assert step.reason == "along the trail"


def test_far_behind_it_runs_at_2x_and_near_it_matches():
    trail = laid_trail(*range(0, 1300, 60))
    f = follower(trail)
    assert f.plan(snap(0, 0), (0, 0, 0), snap(1260, 0), 0).fast
    near = follower(laid_trail(0, 60, 120, 180, 240, 300))
    assert not near.plan(snap(0, 0), (0, 0, 0), snap(300, 0), 0).fast


def test_within_its_gap_of_a_moving_leader_it_stops():
    f = follower(laid_trail(0, 60, 120))
    assert f.plan(snap(0, 0), (0, 0, 0), snap(120, 0), 0).kind is StepKind.STOP


def test_each_follower_keeps_its_own_gap_and_place():
    spacings = [spacing_for(i) for i in range(3)]
    assert len({s.gap for s in spacings}) == 3
    assert len({round(s.angle, 3) for s in spacings}) == 3


def test_around_a_still_leader_it_takes_its_place_behind():
    f = follower(laid_trail(0, 60, 120, 180, 240, 300))
    leader = snap(300, 0)
    f.plan(snap(0, 0), (0, 0, 0), leader, 0.0)
    step = f.plan(snap(0, 0), (0, 0, 0), leader, 2.0)
    assert step.kind is StepKind.WALK and step.reason == "to its place"
    # Yaw 0 faces -y, so behind the leader is +y.
    assert step.target[1] > 0


def test_at_a_jump_it_interacts():
    trail = laid_trail(0, 60, 120)
    trail.note(snap(9000, 9000, t=0.4))
    f = follower(trail)
    step = f.plan(snap(0, 0), (120, 0, 0), snap(9000, 9000), 0)
    assert step.kind is StepKind.INTERACT
    assert step.point.landing == (9000, 9000, 0.0)


def test_a_jump_that_did_not_take_it_falls_back_to_a_port():
    trail = laid_trail(0, 60, 120)
    trail.note(snap(9000, 9000, t=0.4))
    f = follower(trail)
    jump = f.plan(snap(0, 0), (120, 0, 0), snap(9000, 9000), 0).point
    f._jump_failed = jump.seq
    assert f.plan(snap(0, 0), (120, 0, 0), snap(9000, 9000), 0).kind is StepKind.PORT


def test_a_leader_through_a_gate_is_followed_through_it():
    trail = laid_trail(0, 60, 120, 180)
    trail.note(snap(5000, 5000, t=1.0, zone="WizardCity/WC_Hub"))
    f = follower(trail)
    step = f.plan(snap(0, 0), (0, 0, 0), snap(5000, 5000, zone="WizardCity/WC_Hub"), 0)
    assert step.kind is StepKind.WALK and step.reason == "through the gate"
    assert step.target[0] > 180, "it aims past the gate's last point, to walk on through"


def test_a_gate_that_does_not_take_it_falls_back_to_a_port():
    trail = laid_trail(0, 60, 120, 180)
    trail.note(snap(5000, 5000, t=1.0, zone="WizardCity/WC_Hub"))
    f = follower(trail)
    leader = snap(5000, 5000, zone="WizardCity/WC_Hub")
    f.plan(snap(0, 0), (180, 0, 0), leader, 0)
    assert f.plan(snap(0, 0), (180, 0, 0), leader, 60).kind is StepKind.PORT


def test_a_leader_somewhere_the_trail_does_not_reach_is_a_port():
    f = follower(laid_trail(0, 60, zone="Elsewhere"))
    assert f.plan(snap(0, 0), (0, 0, 0), snap(0, 0, zone="Elsewhere"), 0).kind is StepKind.PORT


def test_a_fight_the_leader_is_in_is_joined():
    f = follower(laid_trail(0, 60, 120))
    step = f.plan(snap(0, 0), (0, 0, 0), snap(120, 0, in_battle=True), 0)
    assert step.kind is StepKind.WALK and step.reason == "joining the fight"


@pytest.fixture
def quick(monkeypatch):
    """A follower's waits, shortened so a chase runs in well under a second of real time."""
    monkeypatch.setattr(follow, "FOLLOW_TICK", 0.01)
    monkeypatch.setattr("bartleby.runtime.input.HELD_KEY_REPEAT", 0.01)


async def _drive_leader(leader: SimClient, reader: StateReader, path, speed=600.0):
    """Walk the leader along *path* at *speed*, publishing its snapshots as its reader would."""
    for (x0, y0), (x1, y1) in zip(path, path[1:]):
        steps = max(1, int(math.dist((x0, y0), (x1, y1)) / (speed * 0.01)))
        for i in range(1, steps + 1):
            leader.place((x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps, 0.0))
            leader.yaw = math.atan2(-(x1 - x0), -(y1 - y0)) % math.tau
            reader.publish(position=leader.position, yaw=leader.yaw, zone=leader.zone, loading=False, speed=0)
            await asyncio.sleep(0.01)


async def test_a_follower_walks_the_leaders_trail_around_a_corner(quick):
    arbiter = Arbiter()
    leader, client = SimClient(), SimClient(position=(-150.0, 0.0, 0.0))
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    leader_reader.subscribe(trail.note)
    f = Follower(client, me, leader_reader, trail, arbiter, spacing=spacing_for(0))
    track = []

    async def watch():
        while True:
            track.append(client.position)
            await asyncio.sleep(0.01)

    task = asyncio.create_task(f.run())
    watcher = asyncio.create_task(watch())
    # An L: east 500, then north 500. Straight at the leader would cut the corner.
    await _drive_leader(leader, leader_reader, [(0, 0), (500, 0), (500, 500)])
    await asyncio.sleep(0.6)
    task.cancel()
    watcher.cancel()
    await asyncio.gather(task, watcher, return_exceptions=True)
    assert flat_distance(client.position, leader.position) < 300
    # It went by the corner rather than across it: some point of its track came near (500, 0).
    assert min(flat_distance(p, (500, 0, 0)) for p in track) < 120
    assert not client.held, "it let go of forward when it was cancelled"


async def test_a_follower_runs_at_2x_to_catch_up_and_never_above(quick):
    arbiter = Arbiter()
    leader, client = SimClient(), SimClient()
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    leader_reader.subscribe(trail.note)
    f = Follower(client, me, leader_reader, trail, arbiter, spacing=spacing_for(0))
    task = asyncio.create_task(f.run())
    await _drive_leader(leader, leader_reader, [(0, 0), (1500, 0)], speed=1500)
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    speeds = [r.detail for r in client.inputs if r.kind == "speed"]
    assert 100 in speeds and max(speeds) == 100


def _speed_follower(client: SimClient) -> tuple[Follower, Lease]:
    arbiter, leader_reader, me = Arbiter(), StateReader(SimClient()), StateReader(client)
    follower = Follower(client, me, leader_reader, Trail(), arbiter, spacing=spacing_for(0))
    return follower, Lease(client, Owner.MOVEMENT, ClientInput(client))


async def test_a_follower_waits_for_a_steady_character_before_writing_speed():
    client = SimClient(speed=0)
    follower, lease = _speed_follower(client)
    await follower._set_speed(lease, fast=True)
    assert client.speed == 0
    await follower._set_speed(lease, fast=True)
    assert client.speed == 100
    assert follower.own_speed == 0


async def test_a_follower_never_writes_speed_to_a_logged_out_characters_freed_object():
    client = SimClient(speed=0)
    follower, lease = _speed_follower(client)
    await follower._set_speed(lease, fast=True)
    await follower._set_speed(lease, fast=True)
    writes = len(client.inputs)

    client.log_out()
    for _ in range(3):
        await follower._set_speed(lease, fast=True)
    await follower.restore_speed(lease)
    assert len(client.inputs) == writes
    assert client.speed == 0x1234


async def test_a_follower_gives_speed_back_only_to_the_character_it_was_read_from():
    client = SimClient(speed=10)
    follower, lease = _speed_follower(client)
    await follower._set_speed(lease, fast=True)
    await follower._set_speed(lease, fast=True)
    assert client.speed == 100

    # Another character, whose field happens to hold our value.
    client.character = 777
    await follower.restore_speed(lease)
    assert client.speed == 100


async def test_a_follower_gives_its_own_speed_back():
    client = SimClient(speed=10)
    follower, lease = _speed_follower(client)
    await follower._set_speed(lease, fast=True)
    await follower._set_speed(lease, fast=True)
    await follower.restore_speed(lease)
    assert client.speed == 10


async def test_a_hotkey_preempts_a_walking_follower_which_then_resumes(quick):
    arbiter = Arbiter()
    leader, client = SimClient(), SimClient()
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    leader_reader.subscribe(trail.note)
    f = Follower(client, me, leader_reader, trail, arbiter, spacing=spacing_for(0))
    task = asyncio.create_task(f.run())
    walking = asyncio.create_task(_drive_leader(leader, leader_reader, [(0, 0), (1200, 0)]))
    await asyncio.sleep(0.3)
    async with arbiter.acquire(client, Owner.HOTKEY) as lease:
        assert not client.holding(GameAction.MOVE_FORWARD)
        await lease.input.press(GameAction.INTERACT)
        at_press = client.position
        await asyncio.sleep(0.1)
        assert client.position == at_press, "nothing walks it while the hotkey holds it"
    await walking
    await asyncio.sleep(0.5)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert client.position[0] > at_press[0] + 200, "it went on following afterwards"
