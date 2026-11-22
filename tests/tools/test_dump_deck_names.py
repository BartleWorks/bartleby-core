"""tools/dump_deck_names.py: the .lang parser, what the snapshot keeps, and what --verify reports."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from bartleby.shared.locales import LOCALES
from tools import dump_deck_names as dump


def _lang_bytes(**records: str) -> bytes:
    lines = ["1:DeckNames"]
    for key, text in records.items():
        lines += [key, "a description" if key == "DeckName" else "", text]
    return "﻿".encode("utf-16-le") + ("\r\n".join(lines) + "\r\n").encode("utf-16-le")


ENGLISH = {
    "Type_10": "3v3 ",
    "Class_7": "Storm ",
    "DeckName": "$PART0$$PART1$Deck $PART2$",
    "Class_0": "",
    "Type_2": "Boss ",
    "Type_0": "",
    "Num_0": "",
    "Num_1": "1",
    "None": "(None)",
    "WindowTitle": "<center>Name Your Deck</center>",
    "00000006": "",
}


def _archive(**locales: dict[str, str]) -> dict[str, bytes]:
    """A Root.wad with each locale's table, en-US's where *locales* names none (by its name's underscores)."""
    return {
        f"Locale/{locale}/DeckNames.lang": _lang_bytes(**locales.get(locale.replace("-", "_"), ENGLISH))
        for locale in LOCALES
    }


def _snapshot(**locales: dict[str, str]) -> dict:
    return dump.build_snapshot(_archive(**locales), Path("Root.wad"))


def test_parse_lang_reads_described_and_undescribed_records_alike():
    table = dump.parse_lang(_lang_bytes(**ENGLISH))

    assert table["DeckName"] == "$PART0$$PART1$Deck $PART2$"
    assert table["Type_2"] == "Boss "
    assert table["Class_0"] == ""


def test_a_table_keeps_the_composer_the_empty_choice_and_the_wheels_in_order():
    table = dump.deck_name_table(ENGLISH)

    assert list(table) == ["DeckName", "None", "Class_7", "Type_2", "Type_10", "Num_1"]
    # As the game has it, its trailing space included: that is what separates the parts.
    assert table["Type_2"] == "Boss "


def test_the_snapshot_holds_each_locales_own_table():
    german = {k: v for k, v in ENGLISH.items() if k != "Class_7"}
    snapshot = _snapshot(de=german)

    assert list(snapshot["locales"]) == list(LOCALES)
    assert "Class_7" in snapshot["locales"]["en-US"]
    assert "Class_7" not in snapshot["locales"]["de"]


def test_a_locale_the_archive_lacks_is_reported(capsys):
    archive = _archive()
    del archive["Locale/pl/DeckNames.lang"]
    snapshot = dump.build_snapshot(archive, Path("Root.wad"))

    assert "pl" not in snapshot["locales"]
    assert "Locale/pl/DeckNames.lang" in capsys.readouterr().out
    assert dump.verify(snapshot) == ["pl: no table"]


def test_a_whole_snapshot_verifies():
    assert dump.verify(_snapshot()) == []


def test_a_locale_with_fewer_choices_verifies():
    german = {k: v for k, v in ENGLISH.items() if k != "Type_10"}
    assert dump.verify(_snapshot(de=german)) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"DeckName": "Deck $PART0$"}, "DeckName 'Deck $PART0$' lacks $PART1$, $PART2$"),
        ({"None": ""}, "no text for the empty choice"),
        ({"Num_1": ""}, "the Num wheel is empty"),
        ({"Class_99": "Neu "}, "Class_99 is not in en-US"),
    ],
)
def test_verify_reports_what_a_locale_cannot_answer(change, problem):
    assert dump.verify(_snapshot(de=ENGLISH | change)) == [f"de: {problem}"]


def test_verify_reports_a_file_edited_by_hand():
    snapshot = _snapshot()
    snapshot["locales"]["de"] |= {"Storm": "Sturm", "Class_7": " "}

    assert dump.verify(snapshot) == ["de: Class_7 has no text", "de: 'Storm' is on no wheel"]


def test_differences_name_each_key_the_game_has_changed():
    old, new = _snapshot(), _snapshot(it=ENGLISH | {"Type_2": "Capo ", "Class_8": "Città "})

    assert dump.differences(old, new) == [
        "it: Class_8 is None here and 'Città ' in the game",
        "it: Type_2 is 'Boss ' here and 'Capo ' in the game",
    ]
    assert dump.differences(new, new) == []


def _run(monkeypatch, *argv) -> int:
    monkeypatch.setattr(sys, "argv", ["dump_deck_names.py", *map(str, argv)])
    return dump.main()


@pytest.fixture
def wad(monkeypatch, tmp_path) -> Path:
    """A Root.wad to pass as --wad, whose content is :func:`_archive`'s."""
    path = tmp_path / "Root.wad"
    path.write_bytes(b"")
    monkeypatch.setattr(dump, "Archive", type("Archive", (), {"mmap": staticmethod(lambda _path: _archive())}))
    return path


def test_the_tool_writes_a_snapshot_that_verifies_against_its_wad(monkeypatch, tmp_path, wad):
    out = tmp_path / "data" / "deck_names.json"

    assert _run(monkeypatch, "--wad", wad, "--out", out) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["locales"]["pl"]["Type_2"] == "Boss "
    assert _run(monkeypatch, "--wad", wad, "--out", out, "--verify") == 0


def test_verify_fails_on_a_snapshot_older_than_the_game(monkeypatch, tmp_path, wad, capsys):
    out = tmp_path / "deck_names.json"
    out.write_text(json.dumps(_snapshot(de=ENGLISH | {"Type_2": "Anführer"})), encoding="utf-8")

    assert _run(monkeypatch, "--wad", wad, "--out", out, "--verify") == 1
    assert "de: Type_2 is 'Anführer' here and 'Boss ' in the game" in capsys.readouterr().out


def test_verify_checks_the_file_alone_where_the_game_is_not_installed(monkeypatch, tmp_path, capsys):
    out = tmp_path / "deck_names.json"
    out.write_text(json.dumps(_snapshot()), encoding="utf-8")

    assert _run(monkeypatch, "--wad", tmp_path / "gone.wad", "--out", out, "--verify") == 0
    assert "the file alone was checked" in capsys.readouterr().out


def test_the_tool_writes_nothing_without_a_wad(monkeypatch, tmp_path):
    out = tmp_path / "deck_names.json"

    assert _run(monkeypatch, "--wad", tmp_path / "gone.wad", "--out", out) == 1
    assert not out.exists()
    assert _run(monkeypatch, "--wad", tmp_path / "gone.wad", "--out", out, "--verify") == 1
