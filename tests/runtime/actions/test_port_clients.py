"""Tests for node_helpers.port_clients and gather_at_player: what counts as a port having happened.

A port shows a loading screen only when it changes zone or realm. Within one zone and realm the
player's position jumps onto the target instead, and a port onto someone already beside you shows
nothing at all (#14, #69).
"""

from unittest.mock import AsyncMock, patch

import pytest

import bartleby.runtime.actions.node_helpers as nh
from bartleby.runtime.game import wizard_names
from tests.port_sim import PortSim

_NAME = "Luke SkullHunter"
_AT = (100.0, 200.0, 0.0)
_BESIDE = (103.0, 200.0, 0.0)
_FAR = (900.0, -400.0, 0.0)


@pytest.fixture(autouse=True)
def _no_dialogs_and_no_names_left_over():
    with patch.object(nh, "resolve_modal", new=AsyncMock(return_value=nh.ModalOutcome(answered=False))):
        yield
    wizard_names.clear_cache()


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(nh, "TRIGGER_TIMEOUT", 0.05)
    monkeypatch.setattr(nh, "PORT_QUIET_GRACE", 0.05)


def _sim() -> PortSim:
    sim = PortSim()
    sim.target = sim.player("Dst", _AT, name=_NAME)
    return sim


async def _port(sim: PortSim, movers: list, **kwargs) -> nh.PortToFriendResult:
    kwargs.setdefault("trigger_timeout", 0.05)
    kwargs.setdefault("ceiling", 5.0)
    kwargs.setdefault("poll_interval", 0.001)
    kwargs.setdefault("quiet_grace", 0.05)
    return await nh.port_clients(movers, lambda c: sim.teleport(c, _NAME.lower()), **kwargs)


# ---------------------------------------------------------------------------
# port_clients
# ---------------------------------------------------------------------------


async def test_a_loading_screen_is_a_port():
    sim = _sim()
    mover = sim.player("Src", _FAR)

    result = await _port(sim, [mover])

    assert result.seen_loading == {0} and not result.jumped and not result.presumed
    assert result.missing(1) == []
    assert sim.times_ported(mover) == 1


async def test_a_jump_with_no_loading_screen_is_a_port():
    """#69: the same zone and realm never loads, and was re-ported until the node gave up."""
    sim = _sim()
    mover = sim.player("Dst", _FAR)

    result = await _port(sim, [mover])

    assert result.jumped == {0} and not result.seen_loading
    assert sim.times_ported(mover) == 1


async def test_a_client_that_may_be_there_is_reissued_once_then_presumed():
    sim = _sim()
    mover = sim.player("Dst", _BESIDE)

    result = await _port(sim, [mover], maybe_there={0})

    assert result.presumed == {0}
    assert result.missing(1) == []
    assert sim.times_ported(mover) == 2, "the first port, then one re-issue in case it never registered"


async def test_a_client_that_may_be_there_but_loads_is_not_presumed():
    """Beside the target on another realm: the same coordinates, but the port still loads."""
    sim = _sim()
    mover = sim.player("Dst", _AT, realm="elsewhere")

    result = await _port(sim, [mover], maybe_there={0})

    assert result.seen_loading == {0} and not result.presumed
    assert sim.times_ported(mover) == 1


async def test_a_quiet_client_not_flagged_is_never_presumed_and_is_reissued_once_per_window():
    """The retry used to fire on every poll once the first window had passed."""
    sim = _sim()
    mover = sim.player("Src", _FAR, port="ignored")

    result = await _port(sim, [mover], ceiling=0.3)

    assert result.missing(1) == [0]
    assert 2 <= sim.times_ported(mover) <= 1 + 0.3 / 0.05 + 2


async def test_an_unreadable_position_is_not_mistaken_for_a_jump():
    sim = _sim()
    mover = sim.player("Dst", _FAR, port="ignored")
    mover.body.position = AsyncMock(side_effect=RuntimeError("body not readable"))

    result = await _port(sim, [mover], ceiling=0.1)

    assert result.missing(1) == [0]


async def test_with_a_target_a_quiet_client_is_presumed_only_once_seen():
    sim = _sim()
    mover = sim.player("Dst", _BESIDE)

    result = await _port(sim, [mover], maybe_there={0}, target=sim.target)

    assert result.presumed == {0}


async def test_with_a_target_a_quiet_client_on_another_realm_is_never_presumed():
    """Beside the target on another realm, with a port that never registers (#130).

    Standing beside it is the zone and the spot; only being seen is the realm. Without the
    target this is presumed there, and the player goes on to fight or enter alone.
    """
    sim = _sim()
    mover = sim.player("Dst", _BESIDE, realm="elsewhere", port="ignored")

    blind = await _port(sim, [mover], maybe_there={0}, ceiling=0.3)
    seen = await _port(sim, [mover], maybe_there={0}, ceiling=0.3, target=sim.target)

    assert blind.presumed == {0}
    assert not seen.presumed and seen.missing(1) == [0]
    assert sim.times_ported(mover) > 4, "not presumed, so re-issued once per window like any other"


def test_missing_lists_only_clients_that_showed_nothing():
    result = nh.PortToFriendResult({0}, jumped=frozenset({1}), presumed=frozenset({2}))
    assert result.missing(4) == [3]


async def test_one_port_that_cannot_start_leaves_the_others_to_land():
    """The first error used to end the whole start, and a player who had ported was
    counted as not having started, then sent to travel there with the rest."""
    sim = _sim()
    landed, failed, also_landed = (
        sim.player("Src", _FAR),
        sim.player("Src", _FAR, port="fails"),
        sim.player("Src", _FAR),
    )

    result = await _port(sim, [landed, failed, also_landed])

    assert result.seen_loading == {0, 2}
    assert result.unstarted == {1}
    assert result.missing(3) == [1]
    assert landed.where[0] == "Dst" and also_landed.where[0] == "Dst"


async def test_no_port_that_can_start_raises():
    sim = _sim()
    movers = [sim.player("Src", _FAR, port="fails"), sim.player("Src", _FAR, port="fails")]

    with pytest.raises(ValueError):
        await _port(sim, movers)


async def test_a_reissue_that_cannot_start_is_not_tried_again(fast):
    sim = _sim()
    straggler = sim.player("Src", _FAR, port="ignored")
    other = sim.player("Src", _FAR)
    sim.fail_from_call = 3  # both first ports start; the straggler's re-issue does not

    result = await _port(sim, [straggler, other], ceiling=0.5)

    assert result.unstarted == {0}
    assert result.seen_loading == {1}
    assert sim.times_ported(straggler) == 2


# ---------------------------------------------------------------------------
# gather_at_player
# ---------------------------------------------------------------------------
