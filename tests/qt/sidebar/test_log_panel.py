"""The Log tab's minimum-level filter applies to lines already shown (BartleBot #259)."""

import logging

from bartleby.qt.app.logging_setup import LogEntry
from bartleby.qt.sidebar.log_panel import LogPanel


class _Settings:
    """What the panel reads of :class:`~bartleby.qt.app.logging_setup.LogSettings`."""

    record_combat_decisions = False
    log_max_lines = 5000


def _entry(text: str, level: int) -> LogEntry:
    return LogEntry(time=0.0, level=level, logger="sample", message=text, slot=None, text=text)


def _shown(panel: LogPanel) -> list[str]:
    return panel._log.toPlainText().splitlines()


def _fill(panel: LogPanel) -> None:
    panel.append_entry(_entry("debug line", logging.DEBUG))
    panel.append_entry(_entry("info line", logging.INFO))
    panel.append_entry(_entry("warn line", logging.WARNING))
    panel.append_entry(_entry("error line", logging.ERROR))


def test_defaults_to_warning(qapp):
    panel = LogPanel(_Settings())
    _fill(panel)
    assert _shown(panel) == ["warn line", "error line"]


def test_widening_the_filter_brings_back_hidden_lines(qapp):
    panel = LogPanel(_Settings())
    _fill(panel)
    panel._log_level_combo.setCurrentText("DEBUG")
    assert _shown(panel) == ["debug line", "info line", "warn line", "error line"]


def test_narrowing_the_filter_hides_shown_lines(qapp):
    panel = LogPanel(_Settings())
    panel._log_level_combo.setCurrentText("DEBUG")
    _fill(panel)
    panel._log_level_combo.setCurrentText("ERROR")
    assert _shown(panel) == ["error line"]


def test_clear_drops_filtered_lines_too(qapp):
    panel = LogPanel(_Settings())
    _fill(panel)
    panel.clear()
    panel._log_level_combo.setCurrentText("DEBUG")
    assert _shown(panel) == []


def test_a_new_line_limit_applies_to_the_lines_kept(qapp):
    settings = _Settings()
    panel = LogPanel(settings)
    panel._log_level_combo.setCurrentText("DEBUG")
    settings.log_max_lines = 2
    panel.apply_settings()

    _fill(panel)

    assert [entry.text for entry in panel._entries] == ["warn line", "error line"]
    assert panel._log.maximumBlockCount() == 2


def test_each_level_has_its_own_color(qapp):
    panel = LogPanel(_Settings())
    panel._log_level_combo.setCurrentText("DEBUG")
    _fill(panel)

    document = panel._log.document()
    colors = {
        document.findBlockByNumber(i).begin().fragment().charFormat().foreground().color().name() for i in range(4)
    }

    assert len(colors) == 4
