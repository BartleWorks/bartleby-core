"""login_gate: which new windows the client manager leaves unhooked while a launch logs in."""

from bartleby.runtime.launcher.login_gate import LoginGate


def test_nothing_is_held_by_default():
    assert not LoginGate().holds(1)


def test_every_window_is_held_while_a_launch_spawns():
    gate = LoginGate()
    with gate.spawning():
        assert gate.holds(1) and gate.holds(2)
    assert not gate.holds(1)


def test_two_launches_spawning_at_once_hold_until_both_are_done():
    gate = LoginGate()
    with gate.spawning():
        with gate.spawning():
            pass
        assert gate.holds(1)
    assert not gate.holds(1)


def test_a_spawn_that_raises_still_lets_go():
    gate = LoginGate()
    try:
        with gate.spawning():
            raise RuntimeError
    except RuntimeError:
        pass
    assert not gate.holds(1)


def test_a_claimed_window_is_held_alone_until_released():
    gate = LoginGate()
    gate.hold([11, 12])
    assert gate.holds(11) and gate.holds(12) and not gate.holds(13)
    gate.release([11])
    assert not gate.holds(11) and gate.holds(12)
