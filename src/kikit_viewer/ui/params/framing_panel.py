from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.config.schema import SECTIONS, Field
from kikit_viewer.ui.params.base_panel import _set_widget_value

class _CornerTreatmentWidget(QWidget):
    """
    Mutually exclusive corner treatment selector for the framing section.

    Presents a mode combo (none / chamfer / fillet) and shows only the
    relevant spinboxes, ensuring KiKit never receives non-zero values for
    both chamfer and fillet simultaneously.
    """

    changed = Signal(float, float, float)  # chamferwidth, chamferheight, fillet

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._combo = QComboBox()
        self._combo.addItems(["none", "chamfer", "fillet"])
        layout.addWidget(self._combo)

        # Chamfer sub-form
        self._chamfer_box = QWidget()
        chamfer_form = QFormLayout(self._chamfer_box)
        chamfer_form.setContentsMargins(0, 2, 0, 0)
        chamfer_form.setSpacing(4)
        self._cw_spin = self._make_spin()
        self._ch_spin = self._make_spin()
        chamfer_form.addRow("Width", self._cw_spin)
        chamfer_form.addRow("Height", self._ch_spin)
        layout.addWidget(self._chamfer_box)

        # Fillet sub-form
        self._fillet_box = QWidget()
        fillet_form = QFormLayout(self._fillet_box)
        fillet_form.setContentsMargins(0, 2, 0, 0)
        fillet_form.setSpacing(4)
        self._f_spin = self._make_spin()
        fillet_form.addRow("Radius", self._f_spin)
        layout.addWidget(self._fillet_box)

        self._combo.currentTextChanged.connect(self._on_mode_changed)
        self._cw_spin.editingFinished.connect(self._emit)
        self._ch_spin.editingFinished.connect(self._emit)
        self._f_spin.editingFinished.connect(self._emit)

        self._update_visibility("none")

    @staticmethod
    def _make_spin() -> QDoubleSpinBox:
        w = QDoubleSpinBox()
        w.setDecimals(3)
        w.setSingleStep(0.1)
        w.setRange(0.0, 100.0)
        w.setSuffix(" mm")
        return w

    def _on_mode_changed(self, mode: str) -> None:
        self._update_visibility(mode)
        if mode == "none":
            self._cw_spin.setValue(0.0)
            self._ch_spin.setValue(0.0)
            self._f_spin.setValue(0.0)
        self._emit()

    def _update_visibility(self, mode: str) -> None:
        self._chamfer_box.setVisible(mode == "chamfer")
        self._fillet_box.setVisible(mode == "fillet")

    def _emit(self) -> None:
        mode = self._combo.currentText()
        if mode == "chamfer":
            self.changed.emit(self._cw_spin.value(), self._ch_spin.value(), 0.0)
        elif mode == "fillet":
            self.changed.emit(0.0, 0.0, self._f_spin.value())
        else:
            self.changed.emit(0.0, 0.0, 0.0)

    def set_values(self, chamferwidth: float, chamferheight: float, fillet: float) -> None:
        if fillet > 0:
            mode = "fillet"
        elif chamferwidth > 0 or chamferheight > 0:
            mode = "chamfer"
        else:
            mode = "none"

        for w in (self._combo, self._cw_spin, self._ch_spin, self._f_spin):
            w.blockSignals(True)
        try:
            idx = self._combo.findText(mode)
            if idx >= 0:
                self._combo.setCurrentIndex(idx)
            self._cw_spin.setValue(chamferwidth)
            self._ch_spin.setValue(chamferheight)
            self._f_spin.setValue(fillet)
            self._update_visibility(mode)
        finally:
            for w in (self._combo, self._cw_spin, self._ch_spin, self._f_spin):
                w.blockSignals(False)


_SECTIONS = [
    ("framing", "Framing"),
    ("fiducials", "Fiducials"),
    ("tooling", "Tooling Holes"),
]


class FramingPanel(QWidget):
    """
    Combined parameter panel for the Framing, Tooling Holes, and Fiducials
    config sections, presented as stacked QGroupBox sections in one scroll area.
    """

    def __init__(self, model: ConfigModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._widgets: dict[tuple[str, str], QWidget] = {}  # (section, key) → widget
        self._refreshing = False
        self._dependent_groups: list[QGroupBox] = []  # greyed out when framing type = "none"

        self._corner_widget = _CornerTreatmentWidget()
        self._corner_widget.changed.connect(self._on_corner_changed)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(4, 4, 4, 4)
        inner_layout.setSpacing(8)

        for section, title in _SECTIONS:
            group = QGroupBox(title)
            form = QFormLayout(group)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
            form.setContentsMargins(8, 8, 8, 8)
            form.setSpacing(4)

            for field in SECTIONS.get(section, []):
                if section == "framing" and field.key in ("chamferwidth", "chamferheight", "fillet"):
                    if field.key == "chamferwidth":
                        form.addRow(QLabel("Corner treatment"), self._corner_widget)
                    continue
                widget = self._make_widget(section, field)
                if widget is None:
                    continue
                self._widgets[(section, field.key)] = widget
                label = QLabel(field.label)
                if field.tooltip:
                    label.setToolTip(field.tooltip)
                    widget.setToolTip(field.tooltip)
                form.addRow(label, widget)

            inner_layout.addWidget(group)
            if section in ("tooling", "fiducials"):
                self._dependent_groups.append(group)

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

    def _make_widget(self, section: str, field: Field) -> QWidget | None:
        if field.type == "choice":
            w = QComboBox()
            for c in field.choices:
                w.addItem(c)
            w.currentTextChanged.connect(lambda v, s=section, k=field.key: self._on_change(s, k, v))
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
            w.editingFinished.connect(lambda s=section, k=field.key, w=w: self._on_change(s, k, w.value()))
            return w

        if field.type == "int":
            w = QSpinBox()
            w.setRange(
                int(field.min_val) if field.min_val is not None else 0,
                int(field.max_val) if field.max_val is not None else 9999,
            )
            if field.unit:
                w.setSuffix(f" {field.unit}")
            w.editingFinished.connect(lambda s=section, k=field.key, w=w: self._on_change(s, k, w.value()))
            return w

        if field.type == "bool":
            w = QCheckBox()
            w.toggled.connect(lambda v, s=section, k=field.key: self._on_change(s, k, v))
            return w

        return None

    # ------------------------------------------------------------------
    # Two-way binding
    # ------------------------------------------------------------------

    def _on_change(self, section: str, key: str, value: Any) -> None:
        if self._refreshing:
            return
        self._model.set(section, key, value)

    def _on_corner_changed(self, cw: float, ch: float, fillet: float) -> None:
        if self._refreshing:
            return
        self._model.set("framing", "chamferwidth", cw)
        self._model.set("framing", "chamferheight", ch)
        self._model.set("framing", "fillet", fillet)

    def _refresh(self) -> None:
        self._refreshing = True
        try:
            for (section, key), widget in self._widgets.items():
                try:
                    value = self._model.get(section, key)
                except KeyError:
                    continue
                _set_widget_value(widget, value)
        finally:
            self._refreshing = False

        try:
            self._corner_widget.set_values(
                float(self._model.get("framing", "chamferwidth")),
                float(self._model.get("framing", "chamferheight")),
                float(self._model.get("framing", "fillet")),
            )
        except KeyError:
            pass

        has_frame = self._model.get("framing", "type") != "none"
        for group in self._dependent_groups:
            group.setEnabled(has_frame)
