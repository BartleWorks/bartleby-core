"""One record of each wizard's own speed, for every writer of its speed field.

The ledger alone first, then its two writers in BartleBuddy, the Speed toggle and a walking
follower, sharing one.
"""

import asyncio
from types import SimpleNamespace

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

CHARACTER = 7


def _client(hwnd: int = 1):
    return SimpleNamespace(window_handle=hwnd)


def test_the_value_read_before_a_write_is_the_one_given_back():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    assert ledger.own_speed(client) == 40
    assert ledger.to_give_back(client, CHARACTER, 100) == 40
    assert ledger.own_speed(client) is None, "given back, it is forgotten"


def test_a_value_we_wrote_is_never_taken_for_the_games_own():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    # A second writer sharing the ledger reads the field and finds our 100 there.
    ledger.observe(client, CHARACTER, 100)
    assert ledger.own_speed(client) == 40


def test_a_value_the_game_wrote_since_replaces_the_own_one():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    ledger.observe(client, CHARACTER, 55)  # a mount or a zone change rewrote it
    assert ledger.own_speed(client) == 55


def test_nothing_is_given_back_once_the_game_has_written_over_ours():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    assert ledger.to_give_back(client, CHARACTER, 55) is None


def test_nothing_is_given_back_to_another_character():
    ledger, client = SpeedLedger(), _client()
    ledger.observe(client, CHARACTER, 40)
    ledger.wrote(client, CHARACTER, 100)
    assert not ledger.wrote_to(client, CHARACTER + 1)
    assert ledger.to_give_back(client, CHARACTER + 1, 100) is None


def test_it_lists_the_clients_written_and_not_given_back():
    ledger, a, b = SpeedLedger(), _client(1), _client(2)
    for client in (a, b):
        ledger.observe(client, CHARACTER, 0)
        ledger.wrote(client, CHARACTER, 100)
    assert ledger.wrote_to(a, CHARACTER)
    ledger.forget(a)
    assert ledger.clients() == [b]
    assert not ledger.wrote_to(a, CHARACTER)


# -- the Speed toggle and a walking follower, on one ledger ------------------------------------

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
