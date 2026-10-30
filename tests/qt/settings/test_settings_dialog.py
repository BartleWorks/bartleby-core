"""Core's Settings dialog over a product's list of tabs."""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget

from bartleby.qt.settings.settings_dialog import SettingsDialog, SettingsTab


class _Page(QWidget):
    def __init__(self, built: list[str], name: str) -> None:
        super().__init__()
        built.append(name)
        self.name = name
        self.applied = 0

    def apply(self) -> None:
        self.applied += 1


def _dialog(built: list[str]) -> SettingsDialog:
    return SettingsDialog(
        [
            SettingsTab("general", "General", lambda: _Page(built, "general")),
            SettingsTab("hotkeys", "Hotkeys", lambda: _Page(built, "hotkeys")),
        ]
    )


def test_the_tabs_are_the_products_in_its_order(qapp):
    dialog = _dialog([])

    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ["General", "Hotkeys"]


def test_a_tab_is_opened_by_its_name(qapp):
    dialog = _dialog([])

    dialog.open_tab("hotkeys")
    assert dialog.tabs.currentIndex() == 1
    dialog.open_tab("no such tab")
    assert dialog.tabs.currentIndex() == 1


def test_a_page_is_built_only_when_asked_for(qapp):
    built: list[str] = []
    dialog = _dialog(built)

    assert built == []
    assert dialog.page(1).name == "hotkeys"
    assert built == ["hotkeys"]


def test_ok_applies_every_page_built_and_saves_the_products_settings(qapp, sample_store):
    built: list[str] = []
    dialog = _dialog(built)
    page = dialog.page(0)

    dialog._on_accept()

    assert page.applied == 1
    assert built == ["general"]
    assert sample_store.saves == 1
