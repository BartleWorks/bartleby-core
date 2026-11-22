"""Asking before a delete, as every surface with one asks."""

from __future__ import annotations

from PyQt6.QtWidgets import QMessageBox, QWidget


def confirm_delete(parent: QWidget | None, title: str, text: str) -> bool:
    """Ask before a delete, with the verb on the button, and Cancel the default."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(text)
    delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
    box.setDefaultButton(box.addButton(QMessageBox.StandardButton.Cancel))
    box.exec()
    return box.clickedButton() is delete
