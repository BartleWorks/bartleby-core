"""No key is pressed as a fixed letter: every game action goes through game_keys.press."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# Keys that are not an action the player can rebind: Space advances dialogue whatever jump is
# on, and Escape closes a page as CloseUI, which the game does not let a player rebind.
FIXED = {"ESC", "SPACEBAR"}


def test_no_game_action_is_pressed_by_a_fixed_key():
    """A Keycode named in bartleby/ presses that key whatever the account has it bound to."""
    found = []
    for path in sorted((ROOT / "bartleby").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in re.findall(r"\bKeycode\.(\w+)", line):
                if name not in FIXED:
                    found.append(f"{path.relative_to(ROOT)}:{number}: Keycode.{name}")
    listing = "\n".join(found)
    assert found == [], f"press these with game_keys.press(client, GameAction...):\n{listing}"
