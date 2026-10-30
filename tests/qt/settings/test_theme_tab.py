"""The Theme tab reads and writes the product's theme through its settings."""

from __future__ import annotations

from bartleby.qt.settings.rows import SettingRow
from bartleby.qt.settings.theme_tab import ThemeTab


def test_it_opens_on_the_products_theme(qapp, sample_store):
    sample_store.theme = "dark"

    assert ThemeTab()._theme_combo.currentData() == "dark"


def test_apply_writes_the_theme_and_its_colors(qapp, sample_store):
    tab = ThemeTab()
    tab._theme_combo.setCurrentIndex(tab._theme_combo.findData("custom"))

    tab.apply()

    assert sample_store.theme == "custom"
    assert sample_store.custom_theme == tab._colors


def test_the_theme_row_names_the_product(qapp, sample_product):
    row = ThemeTab().findChild(SettingRow)

    assert row.summary.text() == f"Which colors {sample_product.name} uses."
