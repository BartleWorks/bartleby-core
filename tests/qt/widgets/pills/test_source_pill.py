"""Tests for the Source pill: its words, its weights, and a stale override's note."""

from __future__ import annotations

from bartleby.qt.widgets.pills.pill import AccentPill, NeutralPill
from bartleby.qt.widgets.pills.source_pill import OFFICIAL_UPDATED_TIP, source_pill
from bartleby.shared.store_files import Origin


def test_a_stale_override_says_so_and_explains(qapp):
    pill = source_pill(Origin.SHIPPED_OVERRIDE, official_updated=True)
    assert pill.text() == "Official (modified) · Official updated"
    assert pill.toolTip() == OFFICIAL_UPDATED_TIP
    assert source_pill(Origin.SHIPPED_OVERRIDE).text() == "Official (modified)"


def test_each_origin_is_weighted_like_a_button(qapp):
    official, modified, custom = (source_pill(o) for o in (Origin.SHIPPED, Origin.SHIPPED_OVERRIDE, Origin.USER))
    assert isinstance(official, AccentPill) and official.filled
    assert isinstance(modified, AccentPill) and not modified.filled
    assert isinstance(custom, NeutralPill)
