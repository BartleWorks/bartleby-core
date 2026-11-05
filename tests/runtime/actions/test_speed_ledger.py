"""One speed field, two writers: the Speed toggle and a walking follower share a ledger."""

import asyncio

import pytest

from bartleby.runtime.actions import helpers
from bartleby.runtime.actions.helpers import Member, SpeedBoost
from bartleby.runtime.actions.speed_ledger import SpeedLedger
from bartleby.runtime.arbiter import Arbiter, Lease, Owner
from bartleby.runtime.input import ClientInput
from bartleby.runtime.movement.follow import Follower, spacing_for
from bartleby.runtime.movement.trail import Trail
from bartleby.runtime.state import StateReader
from bartleby.runtime.testing.sim_client import SimClient

#: The game's own speed for the follower, and for the leader, which differ so a test can tell them apart.
OWN, LEADER_OWN = 10, 20


class _Team:
    """A leader and one follower, the Speed toggle over both, and the follower's walk, on one ledger."""

    def __init__(self, multiplier: float) -> None:
        self.arbiter = Arbiter()
        self.ledger = SpeedLedger()
        self.leader, self.client = SimClient(speed=LEADER_OWN), SimClient(speed=OWN)
        self.walking = False
        self.boost = SpeedBoost(
            self.arbiter,
            lambda: [Member(self.leader, 1), Member(self.client, 2)],
            multiplier=lambda: multiplier,
            ledger=self.ledger,
            skip=lambda client: client is self.client and self.walking,
        )
        leader_reader = StateReader(self.leader)
        leader_reader.publish(position=(0.0, 0.0, 0.0), zone="Z", loading=False, speed=LEADER_OWN)
        self.follower = Follower(
            self.client,
            StateReader(self.client),
            leader_reader,
            Trail(),
            self.arbiter,
            spacing=spacing_for(0),
            walking_speed=lambda: self.boost.value,
            speed_ledger=self.ledger,
        )
        self.lease = Lease(self.client, Owner.MOVEMENT, ClientInput(self.client))

    async def walk(self, *, fast: bool) -> None:
        """One walking tick of the follower's, twice over so its character reads steady."""
        self.walking = True
        await self.follower._set_speed(self.lease, fast=fast)
        await self.follower._set_speed(self.lease, fast=fast)

    async def stand(self) -> None:
        self.walking = False
        await asyncio.sleep(0.01)


@pytest.fixture(autouse=True)
def fast_speed(monkeypatch):
    monkeypatch.setattr(helpers, "SPEED_REAPPLY_INTERVAL", 0.001)


@pytest.mark.parametrize("multiplier, value", [(1.5, 50), (2.0, 100)])
async def test_catching_up_runs_at_2x_walking_follows_the_toggle_and_off_gives_every_wizard_its_own(multiplier, value):
    team = _Team(multiplier)
    await team.boost.set(True)
    await asyncio.sleep(0.01)
    assert (team.leader.speed, team.client.speed) == (value, value)

    await team.walk(fast=True)
    assert team.client.speed == 100, "catching up is 2.0x whatever the toggle says"
    await asyncio.sleep(0.01)
    assert team.client.speed == 100, "the boost leaves a walking follower's field alone"

    await team.walk(fast=False)
    assert team.client.speed == value, "nearer, it walks at the toggle's speed, not the leader's field"

    await team.walk(fast=True)
    await team.stand()
    assert team.client.speed == value, "standing, the boost has it again"

    await team.boost.set(False)
    assert (team.leader.speed, team.client.speed) == (LEADER_OWN, OWN), "neither took the other's value for its own"


async def test_speed_off_gives_back_a_follower_left_at_2x_by_its_own_walk():
    """At 2.0x the boost never writes a field already at 100, so the follower's write is the one given back."""
    team = _Team(2.0)
    await team.boost.set(True)
    await team.walk(fast=True)
    await team.stand()

    await team.boost.set(False)
    assert team.client.speed == OWN


async def test_speed_off_leaves_a_walking_follower_to_its_walk():
    team = _Team(1.5)
    await team.boost.set(True)
    await asyncio.sleep(0.01)
    await team.walk(fast=True)

    await team.boost.set(False)
    assert team.client.speed == 100
    assert team.leader.speed == LEADER_OWN


async def test_with_speed_off_a_follower_matches_the_leaders_field_and_gives_its_own_back():
    team = _Team(1.5)
    await team.walk(fast=False)
    assert team.client.speed == LEADER_OWN

    await team.follower.restore_speed(team.lease)
    assert team.client.speed == OWN


async def test_a_zone_change_s_value_is_the_game_s_own_whoever_wrote_last():
    team = _Team(1.5)
    await team.boost.set(True)
    await team.walk(fast=True)
    team.client.speed = 35  # the game, on a zone change
    await team.stand()

    await team.boost.set(False)
    assert team.client.speed == 35


def test_the_ledger_gives_back_only_over_our_own_value_and_to_the_same_character():
    ledger, client = SpeedLedger(), SimClient()
    ledger.observe(client, 1, OWN)
    ledger.wrote(client, 1, 100)
    ledger.observe(client, 1, 100)
    assert ledger.own_speed(client) == OWN, "our own write is never the game's"
    assert ledger.wrote_to(client, 1) and not ledger.wrote_to(client, 2)
    assert ledger.to_give_back(client, 1, 100) == OWN
    assert ledger.clients() == []

    ledger.observe(client, 1, OWN)
    ledger.wrote(client, 1, 100)
    assert ledger.to_give_back(client, 1, 55) is None, "the game wrote since"
