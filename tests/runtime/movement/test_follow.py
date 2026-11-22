"""Following: the plan from snapshots, and a follower chasing a leader on simulated clients."""

import asyncio
import math

import pytest

from bartleby.runtime.actions import helpers
from bartleby.runtime.actions.helpers import Member
from bartleby.runtime.actions.speed_ledger import SpeedLedger
from bartleby.runtime.arbiter import Arbiter, Lease, Owner
from bartleby.runtime.game.key_bindings import GameAction
from bartleby.runtime.input import ClientInput
from bartleby.runtime.movement import follow
from bartleby.runtime.movement import sigil as sigil_module
from bartleby.runtime.movement.follow import Follower, StepKind
from bartleby.runtime.movement.geometry import flat_distance
from bartleby.runtime.movement.sigil import PromptReads
from bartleby.runtime.movement.trail import Trail
from bartleby.runtime.state import Snapshot, StateReader
from bartleby.runtime.testing.sim_client import SimClient

ZONE = "WizardCity/WC_Streets/Unicorn_Way"


def snap(x, y, t=0.0, zone=ZONE, yaw=0.0, **kw):
    return Snapshot(key=1, time=t, position=(x, y, 0.0), yaw=yaw, zone=zone, **kw)


def follower(trail=None):
    client = SimClient()
    leader = SimClient()
    return Follower(
        client,
        StateReader(client),
        StateReader(leader),
        trail or Trail(),
        Arbiter(),
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


def test_it_walks_right_up_to_the_leader_with_no_wait_for_it_to_stop():
    f = follower(laid_trail(0, 60, 120, 180, 240, 300))
    step = f.plan(snap(180, 0), (180, 0, 0), snap(300, 0), 0.0)
    assert step.kind is StepKind.WALK and step.target == (300, 0, 0.0)


def test_on_the_leaders_spot_it_stands():
    f = follower(laid_trail(0, 60, 120, 180, 240, 300))
    step = f.plan(snap(270, 0), (270, 0, 0), snap(300, 0), 0.0)
    assert (step.kind, step.reason) == (StepKind.STOP, follow.ON_LEADERS_SPOT)


def test_on_the_spot_it_walks_again_only_once_the_leader_is_resume_away():
    """Not at the leader's first step, or it would stop and start at the leader's heels."""
    f = follower(laid_trail(*range(0, 700, 60)))
    assert f.plan(snap(290, 0), (290, 0, 0), snap(300, 0), 0.0).reason == follow.ON_LEADERS_SPOT
    near = snap(290 + follow.RESUME - 10, 0)
    assert f.plan(snap(290, 0), (290, 0, 0), near, 0.1).reason == follow.ON_LEADERS_SPOT
    away = snap(290 + follow.RESUME + 10, 0)
    assert f.plan(snap(290, 0), (290, 0, 0), away, 0.2).kind is StepKind.WALK
    assert f.plan(snap(290, 0), (290, 0, 0), near, 0.3).kind is StepKind.WALK, "walking, it goes on to the spot"


def test_the_way_onto_the_leader_is_its_trail_round_a_corner():
    """Straight at the leader would cut the corner, through whatever wall made the leader turn."""
    trail = Trail()
    for i, (x, y) in enumerate([(x, 0) for x in range(0, 301, 60)] + [(300, y) for y in range(60, 301, 60)]):
        trail.note(snap(x, y, t=i * 0.1))
    f = follower(trail)
    step = f.plan(snap(0, 0), (0, 0, 0), snap(300, 300), 0.0)
    assert step.reason == "along the trail" and step.target[1] == 0


def test_which_way_the_leader_faces_has_no_say_in_where_it_stands():
    """A wizard spawns facing into the room, its back to the door: behind it is the way out."""
    targets = set()
    for yaw in (0.0, math.pi / 2, math.pi):
        f = follower(laid_trail(0, 60, 120, 180, 240, 300))
        targets.add(f.plan(snap(180, 0), (180, 0, 0), snap(300, 0, yaw=yaw), 0.0).target)
    assert targets == {(300, 0, 0.0)}


def test_a_stall_near_the_leader_stands_it_there_until_the_leader_moves():
    f = follower(laid_trail(*range(0, 700, 60)))
    leader = snap(300, 0)
    f.plan(snap(150, 0), (150, 0, 0), leader, 0.0)
    f._stalls, f._stall_at = follow.MAX_STALLS, (200.0, 0.0, 0.0)
    step = f.plan(snap(150, 0), (150, 0, 0), leader, 0.5)
    assert (step.kind, step.reason) == (StepKind.STOP, follow.AS_NEAR_AS_IT_GETS), "not a port"
    assert not f.stalled, "it stands, and nothing says it is stuck"
    drifted = snap(300 + follow.STILL - 5, 0)
    assert f.plan(snap(150, 0), (150, 0, 0), drifted, 5.0).reason == follow.AS_NEAR_AS_IT_GETS

    # The leader walks on: a fresh try at its spot.
    assert f.plan(snap(150, 0), (150, 0, 0), snap(600, 0), 6.0).kind is StepKind.WALK


def test_a_stall_far_back_from_the_leader_still_counts_toward_a_port():
    f = follower(laid_trail(*range(0, 1300, 60)))
    leader = snap(1260, 0)
    f.plan(snap(100, 0), (100, 0, 0), leader, 0.0)
    f._stalls, f._stall_at = follow.MAX_STALLS, (100.0, 0.0, 0.0)
    step = f.plan(snap(100, 0), (100, 0, 0), leader, 0.5)
    assert (step.kind, step.reason) == (StepKind.PORT, "stalled")


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


def waiting(trail=None):
    """A follower made to wait for its leader to move."""
    client, leader = SimClient(), SimClient()
    return Follower(
        client,
        StateReader(client),
        StateReader(leader),
        trail or Trail(),
        Arbiter(),
        wait_for_move=True,
    )


def test_by_default_a_follower_does_not_wait_for_the_leader_to_move():
    f = follower(laid_trail(0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600))
    assert f.plan(snap(0, 0), (0, 0, 0), snap(600, 0), 0).kind is StepKind.WALK
    assert not f.awaiting_move


def test_waiting_for_the_leader_to_move_it_neither_walks_nor_ports():
    """A leader in another zone would be a port at once: a helper that logged in elsewhere."""
    f = waiting(laid_trail(0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600))
    step = f.plan(snap(0, 0), (0, 0, 0), snap(600, 0), 0)
    assert step.kind is StepKind.WAIT and step.reason == follow.AWAITING_MOVE

    far = waiting(laid_trail(0, 60, zone="Elsewhere"))
    assert far.plan(snap(0, 0), (0, 0, 0), snap(0, 0, zone="Elsewhere"), 0).kind is StepKind.WAIT
    assert far.awaiting_move


def test_a_leader_that_drifts_within_still_has_not_moved():
    f = waiting(laid_trail(0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600))
    f.plan(snap(0, 0), (0, 0, 0), snap(600, 0), 0)
    assert f.plan(snap(0, 0), (0, 0, 0), snap(600 + follow.STILL - 1, 0), 1).kind is StepKind.WAIT


@pytest.mark.parametrize(
    "moved",
    [
        snap(600 + follow.STILL + 1, 0),
        snap(600, 0, zone="WizardCity/WC_Hub"),
        snap(600, 0, in_battle=True),
    ],
    ids=["past STILL", "another zone", "into a fight"],
)
def test_once_the_leader_moves_it_follows_and_waits_no_more(moved):
    f = waiting(laid_trail(0, 60, 120, 180, 240, 300, 360, 420, 480, 540, 600))
    f.plan(snap(0, 0), (0, 0, 0), snap(600, 0), 0)

    assert f.plan(snap(0, 0), (0, 0, 0), moved, 1).kind is not StepKind.WAIT
    assert not f.awaiting_move
    assert f.plan(snap(0, 0), (0, 0, 0), snap(600, 0), 2).kind is not StepKind.WAIT, "a leader back where it was"


def test_a_follower_that_stalled_or_waits_to_port_again_says_it_is_stalled():
    f = follower()
    assert not f.stalled
    f._stalls = 1
    assert f.stalled
    f._stalls = 0
    f._port_after = f._clock() + 60
    assert f.stalled


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _fight(clock: _Clock) -> Follower:
    """P2, near a leader that is in a fight."""
    client, leader = SimClient(), SimClient(position=(120.0, 0.0, 0.0))
    f = Follower(
        client,
        StateReader(client),
        StateReader(leader),
        Trail(),
        Arbiter(),
        label="P2",
        clock=clock,
    )
    f.me.publish(position=client.position, zone=client.zone, loading=False)
    f.leader.publish(position=leader.position, yaw=0.0, zone=client.zone, loading=False, in_battle=True)
    return f


async def test_a_walk_into_the_leaders_fight_is_logged_as_it_starts_and_as_it_joins(caplog):
    clock = _Clock()
    f = _fight(clock)
    with caplog.at_level("INFO", logger=follow.__name__):
        assert (await f._next_step()).reason == follow.JOINING_FIGHT
        await f._next_step()
        clock.now = 1.5
        f.me.publish(in_battle=True)
        assert (await f._next_step()).kind is StepKind.WAIT
    assert caplog.messages == ["P2 walks into the leader's fight", "P2 joined the leader's fight after 1.5 s"]


async def test_a_follower_says_it_is_joining_a_fight_only_while_it_walks_in():
    f = _fight(_Clock())
    assert not f.joining_fight
    await f._next_step()
    assert f.joining_fight
    f.me.publish(in_battle=True)
    await f._next_step()
    assert not f.joining_fight


@pytest.mark.parametrize(
    "ends, why",
    [
        ({"in_battle": False}, "the fight ended first"),
        ({"zone": "Elsewhere"}, "the leader is in another zone the trail does not reach"),
    ],
    ids=["the fight ended", "the leader left"],
)
async def test_a_walk_into_the_leaders_fight_that_does_not_join_says_why(caplog, ends, why):
    clock = _Clock()
    f = _fight(clock)
    with caplog.at_level("INFO", logger=follow.__name__):
        await f._next_step()
        clock.now = 4.0
        f.leader.publish(**ends)
        await f._next_step()
    assert caplog.messages[-1] == f"P2 did not join the leader's fight after 4.0 s: {why}"


# -- a dungeon sigil -----------------------------------------------------------------------------


def _sigil_follower(*, held=False):
    """A follower whose leader, at x=300, has stood at a sigil long enough to be at it."""
    f = follower()
    f._hold = lambda: held
    for i in range(10):
        f.sigil.note(i * 0.1, snap(0, 0), snap(300, 0), leader_prompt=True, my_prompt=None)
    return f


def _mine(f, showing, now=1.0, leader_prompt=True, **kw):
    f.sigil.note(now, snap(0, 0), snap(300, 0), leader_prompt=leader_prompt, my_prompt=showing, **kw)


def test_at_a_sigil_a_follower_with_its_prompt_stands_rather_than_take_its_place():
    f = _sigil_follower()
    _mine(f, True)
    step = f.plan(snap(0, 0), (0, 0, 0), snap(300, 0), 1.0)
    assert (step.kind, step.reason) == (StepKind.STOP, follow.AT_SIGIL)


def test_at_a_sigil_a_follower_without_its_prompt_walks_at_the_leader_until_close():
    f = _sigil_follower()
    _mine(f, False)
    step = f.plan(snap(0, 0), (0, 0, 0), snap(300, 0), 1.0)
    assert (step.kind, step.reason, step.target) == (StepKind.WALK, follow.INTO_RANGE, (300, 0, 0.0))
    assert f.plan(snap(0, 0), (260, 0, 0), snap(300, 0), 1.0).reason == follow.AT_SIGIL


def test_at_a_sigil_a_held_follower_without_its_prompt_stands_held():
    f = _sigil_follower(held=True)
    _mine(f, False)
    assert f.plan(snap(0, 0), (0, 0, 0), snap(300, 0), 1.0).reason == follow.HELD


def test_in_its_countdown_a_follower_sends_nothing_whatever_the_leader_does():
    f = _sigil_follower()
    _mine(f, True)
    _mine(f, False, now=1.1, x_on_me=True)
    assert f.entering
    for leader in (snap(300, 0), snap(2500, 0), snap(300, 0, in_battle=True)):
        assert (f.plan(snap(0, 0), (0, 0, 0), leader, 1.2).kind, f.last_step.kind) == (StepKind.WAIT, StepKind.WAIT)


def test_a_cancel_comes_first_even_held():
    f = _sigil_follower(held=True)
    f.sigil._entering_since, f.sigil._entering_zone, f.sigil.cancel_pending = 1.0, ZONE, True
    step = f.plan(snap(0, 0), (0, 0, 0), snap(300, 0), 1.2)
    assert (step.kind, step.reason) == (StepKind.CANCEL, follow.CANCELLING)


async def test_walking_its_prompt_going_is_walking_out_of_range_not_a_press():
    shown = {}
    leader, client = SimClient(position=(300.0, 0.0, 0.0)), SimClient()
    f = Follower(
        client,
        StateReader(client),
        StateReader(leader),
        Trail(),
        Arbiter(),
        sigil_prompts=PromptReads(lambda c: _shown(shown, c), interval=0),
    )
    f.position = (0.0, 0.0, 0.0)
    me, lead = snap(0, 0), snap(300, 0)
    shown.update({leader.window_handle: True, client.window_handle: True})
    await f._read_sigil(me, lead)
    f.last_step = follow.Step(StepKind.WALK, (300, 0, 0), reason="along the trail")
    shown[client.window_handle] = False
    await f._read_sigil(me, lead)
    assert not f.entering


def _sigil_team(shown, arbiter=None):
    """A running follower's parts: its leader at x=300 and itself at 0, both with their prompts."""
    leader, client = SimClient(position=(300.0, 0.0, 0.0), npc_range=True), SimClient(npc_range=True)
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    leader_reader.publish(position=leader.position, yaw=0.0, zone=leader.zone, loading=False, speed=0)
    shown.update({leader.window_handle: True, client.window_handle: True})
    f = Follower(
        client,
        me,
        leader_reader,
        Trail(),
        arbiter or Arbiter(),
        sigil_prompts=PromptReads(lambda c: _shown(shown, c), interval=0),
    )
    return f, leader, client, leader_reader


async def test_interact_pressed_on_it_starts_its_countdown_before_any_reading_shows_it(quick, monkeypatch, caplog):
    """The arbiter's Interact lease is the press: no prompt reading has to catch it first."""
    monkeypatch.setattr(sigil_module, "SIGIL_SETTLE", 0.05)
    shown, arbiter = {}, Arbiter()
    f, leader, client, _ = _sigil_team(shown, arbiter)
    task = asyncio.create_task(f.run())
    try:
        await _until(lambda: f.at_sigil)
        with caplog.at_level("INFO", logger=follow.__name__):
            await helpers.interact(arbiter, [Member(leader, 1), Member(client, 2)])
            await _until(lambda: f.entering)
        assert shown[client.window_handle], "its prompt still reads as showing"
        assert [m for m in caplog.messages if m.endswith(" is entering the dungeon, and holds still for its countdown")]
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_it_holds_while_the_leader_walks_to_its_seat_though_its_own_reading_lags(quick, monkeypatch):
    """In game, 2026-11-20: the leader's prompt read gone before the follower's, and it walked after it."""
    monkeypatch.setattr(sigil_module, "SIGIL_SETTLE", 0.05)
    shown, arbiter = {}, Arbiter()
    f, leader, client, leader_reader = _sigil_team(shown, arbiter)
    task = asyncio.create_task(f.run())
    try:
        await _until(lambda: f.at_sigil)
        sent = len(client.inputs)
        shown[leader.window_handle] = False
        for x in range(360, 900, 60):
            leader_reader.publish(position=(float(x), 0.0, 0.0))
            await asyncio.sleep(0.03)
        assert len(client.inputs) == sent, "nothing sent while the leader walks to its seat"
        await helpers.interact(arbiter, [Member(client, 2)])
        shown[client.window_handle] = False
        await _until(lambda: f.entering)
        assert [r.kind for r in client.inputs[sent:]] == ["press"], "Interact's press, and nothing of its own"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _shown(shown, client):
    return shown[client.window_handle]


async def test_through_a_countdown_it_sends_nothing_and_cancels_with_a_turn_when_the_leader_does(quick, monkeypatch):
    monkeypatch.setattr(sigil_module, "SIGIL_SETTLE", 0.05)
    monkeypatch.setattr(sigil_module, "SIGIL_SEAT_SETTLE", 0.05)
    shown, arbiter = {}, Arbiter()
    f, leader, client, leader_reader = _sigil_team(shown, arbiter)
    task = asyncio.create_task(f.run())
    try:
        await _until(lambda: f.at_sigil)
        await asyncio.sleep(0.05)
        sent = len(client.inputs)
        await asyncio.sleep(0.2)
        assert len(client.inputs) == sent and not client.held, "standing at the sigil, nothing sent"

        # X on both: the prompts go, and the game walks each to its seat.
        await helpers.interact(arbiter, [Member(leader, 1), Member(client, 2)])
        shown.update({leader.window_handle: False, client.window_handle: False})
        await _until(lambda: f.entering)
        sent = len(client.inputs)
        client.place((120.0, 40.0, 0.0))
        leader_reader.publish(position=(340.0, 0.0, 0.0))
        await asyncio.sleep(0.3)
        assert len(client.inputs) == sent, "no key, no facing, no speed through the countdown"
        assert f.entering

        # The leader steps off its seat: the follower turns once, to cancel its own.
        leader_reader.publish(position=(340.0 + sigil_module.SEAT_MOVED + 20, 0.0, 0.0))
        await _until(lambda: client.presses(GameAction.MOVE_LEFT) == 1)
        await _until(lambda: not f.entering)
        assert client.presses(GameAction.MOVE_LEFT) == 1
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _until(predicate, timeout=2.0):
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("it never happened")


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
    f = Follower(client, me, leader_reader, trail, arbiter)
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


# A known flake (BartleWorks/bartleby-core#24): under -n auto load the follower isn't there yet when
# the 0.5 s wait ends, and it passes alone. See tests/CLAUDE.md, "Known flakes are marked".
@pytest.mark.flaky(reruns=2)
async def test_a_follower_is_on_the_spot_of_a_leader_that_stopped_well_inside_a_second(quick):
    arbiter = Arbiter()
    leader, client = SimClient(), SimClient(position=(-150.0, 0.0, 0.0))
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    leader_reader.subscribe(trail.note)
    f = Follower(client, me, leader_reader, trail, arbiter)
    task = asyncio.create_task(f.run())
    try:
        await _drive_leader(leader, leader_reader, [(0, 0), (600, 0)])
        await _until(lambda: f.last_step.reason == follow.ON_LEADERS_SPOT, timeout=0.5)
        assert flat_distance(client.position, leader.position) <= follow.ON_LEADER
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_a_follower_runs_at_2x_to_catch_up_and_never_above(quick):
    arbiter = Arbiter()
    leader, client = SimClient(), SimClient()
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    leader_reader.subscribe(trail.note)
    f = Follower(client, me, leader_reader, trail, arbiter)
    task = asyncio.create_task(f.run())
    await _drive_leader(leader, leader_reader, [(0, 0), (1500, 0)], speed=1500)
    # Waited for, not slept for: on a loaded machine 0.1 s went by before the follower's first step.
    await _until(lambda: any(r.kind == "speed" for r in client.inputs))
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    speeds = [r.detail for r in client.inputs if r.kind == "speed"]
    assert 100 in speeds and max(speeds) == 100


async def test_without_writes_speed_a_follower_catches_up_at_its_own_speed(quick):
    arbiter = Arbiter()
    leader, client = SimClient(), SimClient(speed=0)
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    leader_reader.subscribe(trail.note)
    f = Follower(client, me, leader_reader, trail, arbiter, writes_speed=False)
    task = asyncio.create_task(f.run())
    await _drive_leader(leader, leader_reader, [(0, 0), (1500, 0)], speed=1500)
    await asyncio.sleep(0.1)
    assert f.walking and not f.holds_speed, "walking, but the field is not its own"
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert client.position[0] > 100, "it walked after the leader"
    assert [r for r in client.inputs if r.kind == "speed"] == []
    assert client.speed == 0


async def test_without_writes_speed_a_follower_gives_back_nothing_another_writer_wrote():
    client = SimClient(speed=10)
    ledger = SpeedLedger()
    f = Follower(
        client,
        StateReader(client),
        StateReader(SimClient()),
        Trail(),
        Arbiter(),
        speed_ledger=ledger,
        writes_speed=False,
    )
    # The Speed toggle's write, on the ledger the follower shares.
    ledger.observe(client, client.character, 10)
    client.speed = 50
    ledger.wrote(client, client.character, 50)
    await f.restore_speed(Lease(client, Owner.MOVEMENT, ClientInput(client)))
    assert client.speed == 50
    assert ledger.wrote_to(client, client.character), "the toggle's record is left as it was"


def _speed_follower(client: SimClient) -> tuple[Follower, Lease]:
    arbiter, leader_reader, me = Arbiter(), StateReader(SimClient()), StateReader(client)
    follower = Follower(client, me, leader_reader, Trail(), arbiter)
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
    f = Follower(client, me, leader_reader, trail, arbiter)
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


def made(trail=None, **kwargs):
    """A follower over a fresh pair of simulated clients, with *kwargs* for the new options."""
    client, leader = SimClient(), SimClient()
    return Follower(client, StateReader(client), StateReader(leader), trail or Trail(), Arbiter(), **kwargs)


_LONG = tuple(range(0, 1300, 60))


def test_a_held_follower_stands_where_it_is():
    held = [True]
    f = made(laid_trail(*_LONG), hold=lambda: held[0])
    step = f.plan(snap(0, 0), (0, 0, 0), snap(1260, 0), 0)
    assert step.kind is StepKind.STOP and step.reason == follow.HELD
    assert f.held


def test_a_held_follower_still_joins_the_leaders_fight_and_stays_where_it_leaves_it():
    f = made(laid_trail(0, 60, 120), hold=lambda: True)
    step = f.plan(snap(0, 0), (0, 0, 0), snap(120, 0, in_battle=True), 0)
    assert step.kind is StepKind.WALK and step.reason == "joining the fight"

    assert f.plan(snap(0, 0, in_battle=True), (100, 0, 0), snap(120, 0, in_battle=True), 1).kind is StepKind.WAIT
    after = f.plan(snap(100, 0), (100, 0, 0), snap(120, 0), 2)
    assert after.kind is StepKind.STOP and after.reason == follow.HELD


def test_a_waiting_follower_held_joins_a_fight_too():
    f = made(laid_trail(0, 60, 120), hold=lambda: True, wait_for_move=True)
    f.plan(snap(0, 0), (0, 0, 0), snap(120, 0), 0)
    assert f.plan(snap(0, 0), (0, 0, 0), snap(120, 0, in_battle=True), 1).reason == "joining the fight"


def test_let_go_it_waits_no_more_and_walks_back_to_a_leader_nearby():
    held = [True]
    f = made(laid_trail(*_LONG), hold=lambda: held[0], wait_for_move=True, port_when_far=True)
    f.plan(snap(0, 0), (0, 0, 0), snap(1260, 0), 0)
    held[0] = False

    step = f.plan(snap(0, 0), (0, 0, 0), snap(1260, 0), 1)
    assert step.kind is StepKind.WALK, "a leader within PORT_FAR is walked to, with no wait"
    assert not f.awaiting_move and not f.held


def test_let_go_beyond_port_far_it_ports():
    held = [True]
    f = made(laid_trail(*range(0, 3600, 60)), hold=lambda: held[0], port_when_far=True)
    f.plan(snap(0, 0), (0, 0, 0), snap(3540, 0), 0)
    held[0] = False
    step = f.plan(snap(0, 0), (0, 0, 0), snap(3540, 0), 1)
    assert step.kind is StepKind.PORT and step.reason == "far from the leader"


def test_let_go_within_port_far_it_walks_back_however_far_past_resync():
    """2,000 units walks at 2.0x in under 2 s, where a port costs two loading screens."""
    held = [True]
    f = made(laid_trail(*range(0, 2100, 60)), hold=lambda: held[0], port_when_far=True)
    f.plan(snap(0, 0), (0, 0, 0), snap(2040, 0), 0)
    held[0] = False
    assert f.plan(snap(0, 0), (0, 0, 0), snap(2040, 0), 1).kind is StepKind.WALK


def test_a_new_follower_far_from_its_leader_ports_once_the_leader_has_moved():
    f = made(laid_trail(*range(0, 3600, 60)), wait_for_move=True, port_when_far=True)
    assert f.plan(snap(0, 0), (0, 0, 0), snap(3540, 0), 0).kind is StepKind.WAIT
    assert f.plan(snap(0, 0), (0, 0, 0), snap(3540 + follow.STILL + 1, 0), 1).kind is StepKind.PORT


def test_a_new_follower_near_its_leader_walks_and_asks_only_once():
    f = made(laid_trail(*_LONG), port_when_far=True)
    assert f.plan(snap(0, 0), (0, 0, 0), snap(1260, 0), 0).kind is StepKind.WALK
    # Fallen behind later, it is the trail's own rules that decide, as before.
    assert f.plan(snap(0, 0), (0, 0, 0), snap(1600, 0), 1).kind is StepKind.WALK


def test_without_port_when_far_a_far_follower_walks_as_before():
    f = made(laid_trail(*range(0, 3600, 60)))
    assert f.plan(snap(0, 0), (0, 0, 0), snap(3540, 0), 0).kind is StepKind.WALK


async def test_a_port_that_lands_ends_the_far_check():
    f = made(laid_trail(*range(0, 3600, 60)), port_when_far=True)

    async def port(_lease):
        return True

    f.port = port
    await f._port(Lease(f.client, Owner.MOVEMENT, ClientInput(f.client)), "far from the leader")
    assert f.plan(snap(0, 0), (0, 0, 0), snap(3540, 0), 0).kind is StepKind.WALK


async def test_near_the_leader_it_walks_at_the_walking_speed_it_is_given():
    client = SimClient(speed=0)
    leader = SimClient(speed=30)
    reader = StateReader(leader)
    reader.publish(position=leader.position, zone=leader.zone, loading=False, speed=30)
    f = Follower(client, StateReader(client), reader, Trail(), Arbiter(), walking_speed=lambda: 50)
    lease = Lease(client, Owner.MOVEMENT, ClientInput(client))
    await f._set_speed(lease, fast=False)
    await f._set_speed(lease, fast=False)
    assert client.speed == 50


async def test_with_no_walking_speed_it_matches_the_leaders_field():
    client = SimClient(speed=0)
    reader = StateReader(SimClient())
    reader.publish(position=(0.0, 0.0, 0.0), zone=ZONE, loading=False, speed=30)
    f = Follower(client, StateReader(client), reader, Trail(), Arbiter(), walking_speed=lambda: None)
    lease = Lease(client, Owner.MOVEMENT, ClientInput(client))
    await f._set_speed(lease, fast=False)
    await f._set_speed(lease, fast=False)
    assert client.speed == 30


async def test_a_follower_is_walking_only_while_it_holds_the_movement_lease(quick):
    arbiter = Arbiter()
    leader, client = SimClient(), SimClient()
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    trail = Trail()
    leader_reader.subscribe(trail.note)
    f = Follower(client, me, leader_reader, trail, arbiter)
    assert not f.walking
    task = asyncio.create_task(f.run())
    walking = asyncio.create_task(_drive_leader(leader, leader_reader, [(0, 0), (800, 0)]))
    seen = []
    for _ in range(40):
        seen.append(f.walking and arbiter.holder(client) is not None)
        await asyncio.sleep(0.01)
    await walking
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert any(seen)
    assert not f.walking


async def test_a_follower_with_no_walking_speed_stands_at_its_own_speed(quick):
    """With the Speed toggle off, a field left at the catch-up's 2.0x would be nobody's to give back."""
    arbiter = Arbiter()
    leader, client = SimClient(), SimClient(speed=10)
    leader_reader, me = StateReader(leader), StateReader(client)
    me.publish(position=client.position, zone=client.zone, loading=False, speed=10)
    trail = Trail()
    leader_reader.subscribe(trail.note)
    f = Follower(client, me, leader_reader, trail, arbiter, walking_speed=lambda: None)
    task = asyncio.create_task(f.run())
    await _drive_leader(leader, leader_reader, [(0, 0), (1500, 0)], speed=1500)
    for _ in range(300):
        if not f.walking and 100 in [r.detail for r in client.inputs if r.kind == "speed"]:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert 100 in [r.detail for r in client.inputs if r.kind == "speed"], "it caught up at 2.0x"
    assert client.speed == 10
