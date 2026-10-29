"""The .lang parser in ``tools/dump_dialog_captions.py``.

The middle line of a record is a translator description that is usually, but not
always, blank. A parser that required it blank silently dropped every documented
key — which is how GoToFriendFail came to be written off as unusable, its
description mistaken for its value.
"""

from __future__ import annotations


def _lang_bytes(*lines: str) -> bytes:
    return ("1:GUI\r\n" + "\r\n".join(lines)).encode("utf-16-le")


def test_parse_lang_reads_a_plain_record():
    from tools.dump_dialog_captions import parse_lang

    raw = _lang_bytes("ConnectionLost", "", "Connection Lost!", "")
    assert parse_lang(raw)["ConnectionLost"] == "Connection Lost!"


def test_parse_lang_reads_a_record_whose_description_is_filled_in():
    from tools.dump_dialog_captions import parse_lang

    raw = _lang_bytes(
        "GoToFriendFail",
        "The message that appears when you can't go to a friend.",
        "You failed to teleport to this friend.",
        "",
    )
    table = parse_lang(raw)

    assert table["GoToFriendFail"] == "You failed to teleport to this friend."
    # The description must never be mistaken for the value.
    assert "The message that appears" not in table["GoToFriendFail"]


def test_parse_lang_keeps_records_aligned_across_mixed_descriptions():
    """A described record must not shift every record after it."""
    from tools.dump_dialog_captions import parse_lang

    raw = _lang_bytes(
        "First",
        "",
        "first value",
        "Second",
        "a description",
        "second value",
        "Third",
        "",
        "third value",
        "",
    )
    table = parse_lang(raw)

    assert table["First"] == "first value"
    assert table["Second"] == "second value"
    assert table["Third"] == "third value"
