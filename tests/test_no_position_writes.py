"""Core writes no position: the rule in ``bartleby/runtime/CLAUDE.md``, *Core writes no position*.

A wizard moved by a written position is a teleport, which Walking Routes (BartleBot #322) takes
out of every product. The planner never plans a hop, and the one hop BartleBot keeps is its own
node's, measured where it stands; nothing in core writes one, so nothing in core can be called
to write one. Ports through the friends list or the group menu are the server's own move, and
calling them is not a write.
"""

import ast
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "bartleby"

#: The calls that write a position: wizwalker's client and body, wizsprinter's helper, and any
#: helper named for one.
WRITES = frozenset({"teleport", "tp_to", "write_position", "write_xyz"})


def position_writes(root: Path) -> list[str]:
    """Each call under *root* to one of :data:`WRITES`, as ``file:line name``."""
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in WRITES:
                found.append(f"{path.relative_to(root).as_posix()}:{node.lineno} {name}")
    return found


def test_nothing_in_core_writes_a_position():
    assert position_writes(CORE) == []


@pytest.mark.parametrize(
    "code",
    [
        "async def go(client):\n    await client.teleport(XYZ(0, 0, 0))\n",
        "async def go(client):\n    await client.body.write_position(XYZ(0, 0, 0))\n",
        "async def go(client):\n    await client.tp_to(XYZ(0, 0, 0))\n",
        "def go(body):\n    write_xyz(body, 0, 0, 0)\n",
    ],
)
def test_the_guard_finds_a_planted_position_write(tmp_path, code):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "moves.py").write_text(code, encoding="utf-8")

    assert len(position_writes(tmp_path)) == 1


def test_a_port_through_the_friends_list_is_not_a_write(tmp_path):
    (tmp_path / "ports.py").write_text(
        "async def go(client):\n    await teleport_via_friends_list(client, 'a friend')\n", encoding="utf-8"
    )

    assert position_writes(tmp_path) == []
