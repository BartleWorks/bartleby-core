"""The order a leader's followers walk into its fights in (BartleBuddy #31, Stage 4).

The gate's rules on a clock the test drives, the follower standing at a closed gate, and a
team of followers walking into a fight on simulated clients: in the join order with one, the
nearest first without.
"""

import asyncio
import time

import pytest

from bartleby.runtime.arbiter import Arbiter
from bartleby.runtime.movement import follow
from bartleby.runtime.movement.follow import Follower, StepKind
from bartleby.runtime.movement.geometry import flat_distance
from bartleby.runtime.movement.join_order import JoinOrder
from bartleby.runtime.movement.trail import Trail
from bartleby.runtime.state import Snapshot, StateReader
from bartleby.runtime.testing.sim_client import SimClient

ZONE = "WizardCity/WC_Streets/Unicorn_Way"
HOLD = 4.0


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _leader(in_battle: bool) -> Snapshot:
    return Snapshot(key=1, time=0.0, position=(0.0, 0.0, 0.0), zone=ZONE, in_battle=in_battle)


def _order(*followers, in_battle=()):
    """A join order over *followers*, with those in *in_battle* in the fight, and its clock."""
    clock, fighting = _Clock(), set(in_battle)
    order = JoinOrder(fighting.__contains__, hold=HOLD, clock=clock)
    order.set(followers)
    order.note(_leader(True))
    return order, clock, fighting


# -- the gate -------------------------------------------------------------------------------


def test_the_first_in_the_order_walks_in_at_once_and_the_next_waits_for_it():
    order, _clock, fighting = _order("a", "b", "c")

    assert order.may_join("a")
    assert not order.may_join("b")
    assert not order.may_join("c")

    fighting.add("a")
    assert order.may_join("b")
    assert not order.may_join("c")
    fighting.add("b")
    assert order.may_join("c")


def test_a_follower_ahead_that_never_gets_in_holds_the_next_one_up_for_a_hold_only():
    order, clock, _fighting = _order("a", "b")
    assert order.may_join("a")

    clock.now = HOLD - 0.1
    assert not order.may_join("b")
    clock.now = HOLD
    assert order.may_join("b")


def test_one_ahead_that_never_asks_counts_as_opening_a_hold_late():
    order, clock, _fighting = _order("a", "absent", "c")
    assert order.may_join("a")

    clock.now = 2 * HOLD - 0.1
    assert not order.may_join("c")
    clock.now = 2 * HOLD
    assert order.may_join("c")


def test_a_gate_once_open_stays_open_for_the_fight():
    order, _clock, fighting = _order("a", "b")
    fighting.add("a")
    assert order.may_join("b")

    fighting.discard("a")
    assert order.may_join("b")


def test_the_fights_end_closes_every_gate_for_the_next():
    order, clock, fighting = _order("a", "b")
    order.may_join("a")
    fighting.add("a")
    assert order.may_join("b")

    order.note(_leader(False))
    fighting.clear()
    clock.now = 100.0
    order.note(_leader(True))
    assert not order.may_join("b"), "the next fight starts the order again"
    assert order.may_join("a")


def test_with_no_order_or_out_of_it_a_follower_walks_in_at_once():
    order = JoinOrder(lambda _f: False)
    assert order.may_join("anyone")

    order, _clock, _fighting = _order("a", "b")
    assert order.may_join("not in the order")


def test_a_new_order_takes_effect_at_once():
    order, _clock, _fighting = _order("a", "b")
    order.set(["b", "a"])

    assert order.may_join("b")
    assert not order.may_join("a")


# -- the follower at its gate ---------------------------------------------------------------


def _fight_follower(clock: _Clock, gate) -> Follower:
    client, leader = SimClient(), SimClient(position=(120.0, 0.0, 0.0))
    f = Follower(
        client, StateReader(client), StateReader(leader), Trail(), Arbiter(), label="P2", clock=clock, join_gate=gate
    )
    f.me.publish(position=client.position, zone=client.zone, loading=False)
    f.leader.publish(position=leader.position, yaw=0.0, zone=client.zone, loading=False, in_battle=True)
    return f


async def test_a_follower_stands_at_its_closed_gate_and_counts_as_joining(caplog):
    clock, open_ = _Clock(), [False]
    f = _fight_follower(clock, lambda: open_[0])
    with caplog.at_level("INFO", logger=follow.__name__):
        step = await f._next_step()
        assert step.kind is StepKind.STOP and step.reason == follow.WAITING_TO_JOIN
        assert f.joining_fight, "a fighter's first cast waits for it"
        await f._next_step()

        clock.now = 1.5
        open_[0] = True
        step = await f._next_step()
        assert step.kind is StepKind.WALK and step.reason == follow.JOINING_FIGHT
    assert caplog.messages == [
        "P2 waits its turn to join the leader's fight",
        "P2 walks into the leader's fight after 1.5 s waiting its turn",
    ]


def _gated(gate, **kwargs) -> Follower:
    client, leader = SimClient(), SimClient()
    trail = Trail()
    for i, x in enumerate(range(0, 3600, 60)):
        trail.note(Snapshot(key=1, time=i * 0.1, position=(float(x), 0.0, 0.0), zone=ZONE))
    return Follower(client, StateReader(client), StateReader(leader), trail, Arbiter(), join_gate=gate, **kwargs)


def test_a_far_follower_at_its_closed_gate_waits_rather_than_port_into_the_fight():
    """A port lands beside the fight, and the game pulls it in at once, out of its turn."""
    open_ = [False]
    f = _gated(lambda: open_[0], port_when_far=True)
    me = Snapshot(key=2, position=(0.0, 0.0, 0.0), zone=ZONE)
    fight = Snapshot(key=1, position=(3540.0, 0.0, 0.0), zone=ZONE, in_battle=True)

    step = f.plan(me, (0.0, 0.0, 0.0), fight, 0)
    assert step.kind is StepKind.STOP and step.reason == follow.WAITING_TO_JOIN

    open_[0] = True
    step = f.plan(me, (0.0, 0.0, 0.0), fight, 1)
    assert step.kind is StepKind.PORT, "its turn: far, it ports as before"


def test_a_follower_behind_the_trail_waits_at_its_closed_gate_too():
    f = _gated(lambda: False)
    f.cursor = -1  # fallen behind the trail's start, which is a port
    me = Snapshot(key=2, position=(0.0, 0.0, 0.0), zone=ZONE)
    fight = Snapshot(key=1, position=(600.0, 0.0, 0.0), zone=ZONE, in_battle=True)

    assert f.plan(me, (0.0, 0.0, 0.0), fight, 0).reason == follow.WAITING_TO_JOIN


async def test_a_gate_is_asked_only_while_the_leader_is_in_a_fight():
    asked = []
    f = _fight_follower(_Clock(), lambda: asked.append(1) or False)
    f.leader.publish(in_battle=False)
    await f._next_step()

    assert not asked


# -- a team walking in, on simulated clients ---------------------------------------------------


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(follow, "FOLLOW_TICK", 0.01)
    monkeypatch.setattr("bartleby.runtime.input.HELD_KEY_REPEAT", 0.01)


#: Units from the leader at which a simulated follower steps onto the duel circle.
_CIRCLE = 80.0


async def _walk_in(starts: list[float], order: list[int] | None, *, absent: int | None = None, hold: float = 0.3):
    """Followers starting *starts* units east of a leader in a fight walk in. Returns who joined, in order.

    *order* is the join order by index into *starts*, or None for no gate. The follower at
    *absent* is listed in the order but never runs.
    """
    arbiter = Arbiter()
    leader = SimClient(position=(0.0, 0.0, 0.0))
    leader_reader = StateReader(leader)
    leader_reader.publish(position=leader.position, yaw=0.0, zone=leader.zone, loading=False, in_battle=True)
    clients = [SimClient(position=(x, 0.0, 0.0)) for x in starts]
    readers = [StateReader(c) for c in clients]
    for client, reader in zip(clients, readers):
        reader.publish(position=client.position, zone=client.zone, loading=False, speed=0)
    joins = None
    if order is not None:
        joins = JoinOrder(lambda c: readers[clients.index(c)].latest.in_battle, hold=hold, clock=time.monotonic)
        joins.set([clients[i] for i in order])
        joins.note(leader_reader.latest)
    joined: list[int] = []

    async def circle():
        while len(joined) < len(clients) - (absent is not None):
            for i, (client, reader) in enumerate(zip(clients, readers)):
                if i not in joined and flat_distance(client.position, leader.position) <= _CIRCLE:
                    reader.publish(in_battle=True)
                    joined.append(i)
            await asyncio.sleep(0.005)

    followers = [
        Follower(
            c,
            r,
            leader_reader,
            Trail(),
            arbiter,
            join_gate=None if joins is None else (lambda c=c: joins.may_join(c)),
        )
        for c, r in zip(clients, readers)
    ]
    tasks = [asyncio.create_task(f.run()) for i, f in enumerate(followers) if i != absent]
    try:
        await asyncio.wait_for(circle(), timeout=5.0)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return joined


async def test_without_a_gate_the_nearest_follower_walks_in_first(quick):
    assert await _walk_in([300.0, 500.0, 700.0], None) == [0, 1, 2]


async def test_gated_followers_join_in_the_order_whatever_their_distance(quick):
    # The hitter is nearest, and joins last.
    assert await _walk_in([300.0, 500.0, 700.0], [2, 1, 0]) == [2, 1, 0]


async def test_a_gated_follower_joins_once_its_hold_has_passed_for_one_that_never_comes(quick):
    assert await _walk_in([300.0, 500.0, 700.0], [2, 1, 0], absent=1) == [2, 0]
