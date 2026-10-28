"""Tests for Streamer Mode's name masking.

The setting is the product's, handed in with ``configure``; each test leaves it off.
"""

import pytest

from bartleby.qt import streamer_mode


def _enable() -> None:
    streamer_mode.configure(lambda: True)


def test_off_by_default():
    assert streamer_mode.enabled() is False


def test_shows_the_real_name_when_off():
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Luke SkullHunter"


def test_substitutes_a_positional_placeholder_when_on():
    _enable()
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Character 1"
    assert streamer_mode.display_name("Misha SkullSeer", 3) == "Character 4"


def test_the_placeholder_carries_nothing_of_the_name():
    """The whole point: two wizards sharing a name are still told apart, and neither
    contributes a fragment of itself to what is shown."""
    _enable()
    first = streamer_mode.display_name("Wolf", 0)
    second = streamer_mode.display_name("Wolf", 1)
    assert first != second
    assert "Wolf" not in first + second


def test_an_unknown_name_answers_the_placeholder_either_way():
    """Not knowing a name is not a privacy question, so the setting must not change it —
    and a masked placeholder here would claim a character that may not exist."""
    assert streamer_mode.display_name(None, 0) == "—"
    assert streamer_mode.display_name("", 0) == "—"
    _enable()
    assert streamer_mode.display_name(None, 0) == "—"
    assert streamer_mode.display_name("", 0) == "—"


def test_the_caller_chooses_the_unknown_placeholder():
    assert streamer_mode.display_name(None, 0, placeholder="(none)") == "(none)"


def test_the_setting_is_read_per_call_not_cached():
    """A settings toggle has to take effect without rebuilding the widgets that call
    this, which is the reason masking happens at display time rather than at the source."""
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Luke SkullHunter"
    _enable()
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Character 1"
    streamer_mode.configure(lambda: False)
    assert streamer_mode.display_name("Luke SkullHunter", 0) == "Luke SkullHunter"


@pytest.fixture(autouse=True)
def _streamer_mode_off():
    yield
    streamer_mode.configure(lambda: False)
