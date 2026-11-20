"""The leader's trail: points as it walks, jumps and gates marked, resets cut back."""

from bartleby.runtime.movement.trail import SPACING, Mark, Trail
from bartleby.runtime.state import Snapshot

ZONE = "WizardCity/WC_Streets/Unicorn_Way"


def snap(x, y, t, zone=ZONE, loading=False):
    return Snapshot(key=1, time=t, position=(x, y, 0.0), yaw=0.0, zone=None if loading else zone, loading=loading)


def walk(trail, start, end, t0=0.0, step=60.0, speed=600.0):
    """Feed a straight walk from *start* to *end* along x at *speed*, a reading every *step* units."""
    x, t = start, t0
    while x <= end:
        trail.note(snap(x, 0.0, t))
        x += step
        t += step / speed
    return t


def test_a_walk_leaves_a_point_every_spacing():
    trail = Trail()
    walk(trail, 0, 1200)
    xs = [p.position[0] for p in trail.points]
    assert xs[0] == 0 and xs[-1] == 1200
    assert all(b - a >= SPACING for a, b in zip(xs, xs[1:]))
    assert all(p.mark is Mark.WALK for p in trail.points)


def test_a_zone_change_marks_the_last_point_a_gate():
    trail = Trail()
    t = walk(trail, 0, 600)
    trail.note(snap(0, 0, t + 0.1, loading=True))
    trail.note(snap(5000, 5000, t + 3, zone="WizardCity/WC_Hub"))
    gate = [p for p in trail.points if p.mark is Mark.GATE]
    assert len(gate) == 1 and gate[0].position[0] == 600 and gate[0].zone == ZONE
    assert trail.points[-1].zone == "WizardCity/WC_Hub"


def test_a_jump_within_a_zone_marks_where_it_left_and_where_it_landed():
    trail = Trail()
    t = walk(trail, 0, 600)
    trail.note(snap(9000, 4000, t + 0.1))
    jump = [p for p in trail.points if p.mark is Mark.JUMP]
    assert len(jump) == 1
    assert jump[0].position[0] == 600 and jump[0].landing == (9000, 4000, 0.0)
    assert trail.points[-1].position == (9000, 4000, 0.0)


def test_a_reset_back_onto_the_trail_cuts_it_back_rather_than_jumping():
    trail = Trail()
    t = walk(trail, 0, 1800)
    trail.note(snap(1200, 0, t + 0.05))
    assert all(p.mark is Mark.WALK for p in trail.points)
    assert trail.points[-1].position[0] <= 1200


def test_a_move_across_a_loading_screen_in_the_same_zone_is_a_jump():
    trail = Trail()
    t = walk(trail, 0, 600)
    trail.note(snap(0, 0, t + 0.1, loading=True))
    trail.note(snap(600, 9000, t + 10))
    assert [p.mark for p in trail.points if p.mark is not Mark.WALK] == [Mark.JUMP]


def test_sequence_numbers_survive_dropping_the_oldest_points(monkeypatch):
    trail = Trail()
    walk(trail, 0, 6000)
    first = trail.points[0].seq
    trail.points.popleft()
    assert trail.first_seq == first + 1
    assert trail.at(first) is None
    assert trail.at(first + 1) is trail.points[0]
