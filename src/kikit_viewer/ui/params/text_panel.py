from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QWidget,
)

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.config.schema import Field
from kikit_viewer.ui.params.base_panel import SectionPanel

# KiCad 10 renamed several layers; KiKit still expects the old dot-notation names.
_TO_KIKIT: dict[str, str] = {
    "F_Cu":         "F.Cu",
    "B_Cu":         "B.Cu",
    "Edge_Cuts":    "Edge.Cuts",
    "F_Silkscreen": "F.SilkS",
    "B_Silkscreen": "B.SilkS",
    "F_Fab":        "F.Fab",
    "B_Fab":        "B.Fab",
    "F_Courtyard":  "F.CrtYd",
    "B_Courtyard":  "B.CrtYd",
    "F_Mask":       "F.Mask",
    "B_Mask":       "B.Mask",
    "F_Paste":      "F.Paste",
    "B_Paste":      "B.Paste",
    "Dwgs_User":    "Dwgs.User",
    "Cmts_User":    "Cmts.User",
    "Eco1_User":    "Eco1.User",
    "Eco2_User":    "Eco2.User",
}


def _to_kikit_name(name: str) -> str:
    if name in _TO_KIKIT:
        return _TO_KIKIT[name]
    m = re.match(r"^(In\d+)_Cu$", name)
    if m:
        return f"{m.group(1)}.Cu"
    return name


class _CommittingTextEdit(QPlainTextEdit):
    """QPlainTextEdit that commits only on focus-out or Ctrl+Enter."""

    editing_finished = Signal(str)

    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.editing_finished.emit(self.toPlainText())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.editing_finished.emit(self.toPlainText())
        else:
            super().keyPressEvent(event)


class _FilePickerWidget(QWidget):
    """QLineEdit + browse button for selecting a file path."""

    value_changed = Signal(str)

    def __init__(self, file_filter: str = "Python scripts (*.py)", parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self._edit = QLineEdit()
        self._btn = QPushButton("…")
        self._btn.setFixedWidth(28)
        layout.addWidget(self._edit)
        layout.addWidget(self._btn)
        self._filter = file_filter
        self._btn.clicked.connect(self._browse)
        self._edit.editingFinished.connect(lambda: self.value_changed.emit(self._edit.text()))

    def value(self) -> str:
        return self._edit.text()

    def set_value(self, path: str) -> None:
        self._edit.setText(path)

    def _browse(self) -> None:
        start = self._edit.text()
        path, _ = QFileDialog.getOpenFileName(self, "Select script", start, self._filter)
        if path:
            self._edit.setText(path)
            self.value_changed.emit(path)


class TextPanel(SectionPanel):
    def __init__(self, model: ConfigModel, parent=None) -> None:
        self._label_by_key: dict[str, QLabel] = {}  # must exist before super().__init__ calls _refresh
        super().__init__("text", model, parent)
        # Populate _label_by_key now that the form is built.
        for row in range(self._form.rowCount()):
            litem = self._form.itemAt(row, QFormLayout.ItemRole.LabelRole)
            fitem = self._form.itemAt(row, QFormLayout.ItemRole.FieldRole)
            if litem and fitem:
                lw = litem.widget()
                fw = fitem.widget()
                if isinstance(lw, QLabel):
                    for key, widget in self._widgets.items():
                        if widget is fw:
                            self._label_by_key[key] = lw
                            break

    def _make_widget(self, field: Field):
        if field.key == "text":
            w = _CommittingTextEdit()
            fm = w.fontMetrics()
            doc_margin = int(w.document().documentMargin())
            w.setFixedHeight(fm.lineSpacing() * 3 + doc_margin * 2 + w.frameWidth() * 2 + 2)
            w.editing_finished.connect(lambda v: self._on_change("text", v))
            return w
        if field.key == "layer":
            w = QComboBox()
            w.setEditable(True)
            w.addItem("F.SilkS")
            w.currentTextChanged.connect(lambda v: self._on_change("layer", v))
            return w
        if field.key == "script":
            w = _FilePickerWidget()
            w.value_changed.connect(lambda v: self._on_change("script", v))
            return w
        return super()._make_widget(field)

    def _refresh(self) -> None:
        super()._refresh()
        # _FilePickerWidget is not a standard Qt type; set it manually.
        script_widget = self._widgets.get("script")
        if isinstance(script_widget, _FilePickerWidget):
            try:
                script_widget.set_value(str(self._model.get("text", "script")))
            except KeyError:
                pass
        self._update_enabled_state()

    def _update_enabled_state(self) -> None:
        try:
            text_type = self._model.get("text", "type")
        except KeyError:
            text_type = "none"

        active = text_type != "none"

        for key, widget in self._widgets.items():
            if key == "type":
                continue
            label = self._label_by_key.get(key)
            if key == "text":
                visible = text_type == "simple"
                widget.setVisible(visible)
                if label:
                    label.setVisible(visible)
            elif key == "script":
                visible = text_type == "scripted"
                widget.setVisible(visible)
                if label:
                    label.setVisible(visible)
            else:
                widget.setEnabled(active)
                if label:
                    label.setEnabled(active)

    def populate_layers(self, names: list[str]) -> None:
        """Replace layer combo items with KiKit-compatible names; preserve current selection."""
        combo = self._widgets.get("layer")
        if not isinstance(combo, QComboBox):
            return
        current = combo.currentText()
        kikit_names = [_to_kikit_name(n) for n in names]
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(kikit_names)
        idx = combo.findText(current)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        else:
            combo.setCurrentText(current)
        combo.blockSignals(False)
