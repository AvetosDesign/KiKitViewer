from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from kikit_viewer.ui.canvas.scene import PanelScene


class LayersPanel(QWidget):
    """Checkboxes for per-layer visibility, docked to the right of the canvas."""

    def __init__(self, scene: PanelScene, parent=None) -> None:
        super().__init__(parent)
        self._scene = scene

        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setSpacing(4)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.addStretch()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._content)

    def populate(self, names: list[str]) -> None:
        """Rebuild checkboxes for the given layer names."""
        while self._layout.count() > 1:
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for name in names:
            color = self._scene.layer_color(name)

            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(f"background-color: {color};")

            cb = QCheckBox(name)
            cb.setChecked(self._scene.layer_visible(name))
            cb.toggled.connect(
                lambda checked, n=name: self._scene.set_layer_visible(n, checked)
            )

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            row_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            row_layout.addWidget(swatch)
            row_layout.addWidget(cb)
            row_layout.addStretch()

            self._layout.insertWidget(self._layout.count() - 1, row)
