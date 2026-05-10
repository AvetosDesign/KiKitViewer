from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.config.schema import SECTIONS, Field


class SectionPanel(QWidget):
    """
    Auto-generates a form widget for one KiKit config section.

    Each Field in the schema becomes a labelled row:
      choice       → QComboBox
      float/SLength→ QDoubleSpinBox (mm suffix)
      float/SAngle → QDoubleSpinBox (° suffix)
      int          → QSpinBox
      bool         → QCheckBox
      str          → QLineEdit

    Changes write back to ConfigModel immediately; when the model updates
    from an external source (file load, reset) all widgets refresh.
    """

    def __init__(self, section: str, model: ConfigModel, parent=None) -> None:
        super().__init__(parent)
        self._section = section
        self._model = model
        self._widgets: dict[str, QWidget] = {}
        self._refreshing = False

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)
        inner_layout.setSpacing(0)

        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self._form.setContentsMargins(8, 8, 8, 8)
        self._form.setSpacing(4)

        fields = SECTIONS.get(section, [])
        for field in fields:
            widget = self._make_widget(field)
            if widget is None:
                continue
            self._widgets[field.key] = widget
            label = QLabel(field.label)
            if field.tooltip:
                label.setToolTip(field.tooltip)
                widget.setToolTip(field.tooltip)
            self._form.addRow(label, widget)

        inner_layout.addLayout(self._form)
        inner_layout.addStretch()
        scroll.setWidget(inner)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(scroll)

        model.config_changed.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------
    # Widget factory
    # ------------------------------------------------------------------

    def _make_widget(self, field: Field) -> QWidget | None:
        if field.type == "choice":
            w = QComboBox()
            for c in field.choices:
                w.addItem(c)
            w.currentTextChanged.connect(lambda v, k=field.key: self._on_change(k, v))
            return w

        if field.type == "float":
            w = QDoubleSpinBox()
            w.setDecimals(3)
            w.setSingleStep(0.1)
            w.setRange(
                field.min_val if field.min_val is not None else -9999.0,
                field.max_val if field.max_val is not None else 9999.0,
            )
            if field.unit:
                w.setSuffix(f" {field.unit}")
            w.editingFinished.connect(lambda k=field.key, w=w: self._on_change(k, w.value()))
            return w

        if field.type == "int":
            w = QSpinBox()
            w.setRange(
                int(field.min_val) if field.min_val is not None else 0,
                int(field.max_val) if field.max_val is not None else 9999,
            )
            if field.unit:
                w.setSuffix(f" {field.unit}")
            w.editingFinished.connect(lambda k=field.key, w=w: self._on_change(k, w.value()))
            return w

        if field.type == "bool":
            w = QCheckBox()
            w.toggled.connect(lambda v, k=field.key: self._on_change(k, v))
            return w

        if field.type == "str":
            w = QLineEdit()
            w.editingFinished.connect(
                lambda k=field.key, w=w: self._on_change(k, w.text())
            )
            return w

        return None

    # ------------------------------------------------------------------
    # Two-way binding
    # ------------------------------------------------------------------

    def _on_change(self, key: str, value: Any) -> None:
        if self._refreshing:
            return
        self._model.set(self._section, key, value)

    def _refresh(self) -> None:
        """Pull current model values into all widgets."""
        self._refreshing = True
        try:
            for key, widget in self._widgets.items():
                try:
                    value = self._model.get(self._section, key)
                except KeyError:
                    continue
                _set_widget_value(widget, value)
        finally:
            self._refreshing = False


def _set_widget_value(widget: QWidget, value: Any) -> None:
    if isinstance(widget, QComboBox):
        idx = widget.findText(str(value))
        if idx >= 0:
            widget.setCurrentIndex(idx)
        elif widget.isEditable():
            widget.setCurrentText(str(value))
    elif isinstance(widget, QDoubleSpinBox):
        widget.setValue(float(value))
    elif isinstance(widget, QSpinBox):
        widget.setValue(int(value))
    elif isinstance(widget, QCheckBox):
        widget.setChecked(bool(value))
    elif isinstance(widget, QPlainTextEdit):
        widget.blockSignals(True)
        widget.setPlainText(str(value))
        widget.blockSignals(False)
    elif isinstance(widget, QLineEdit):
        widget.setText(str(value))
