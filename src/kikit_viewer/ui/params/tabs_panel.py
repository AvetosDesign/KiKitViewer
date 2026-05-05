from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.config.schema import TABS_FIELDS
from kikit_viewer.ui.params.base_panel import _set_widget_value

# Keys shown for all non-manual tab types
_STANDARD_KEYS = ["hwidth", "vwidth", "hcount", "vcount", "spacing",
                   "mindistance", "fillet"]
_FIELD_BY_KEY = {f.key: f for f in TABS_FIELDS}


class _TabListWidget(QListWidget):
    """QListWidget that emits delete_requested when Delete is pressed."""
    delete_requested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete and self.currentRow() >= 0:
            self.delete_requested.emit()
        else:
            super().keyPressEvent(event)


class TabsPanel(QWidget):
    """
    Parameter panel for the tabs config section.

    Standard mode (type ≠ "manual"): shows the normal spinboxes.
    Manual mode (type = "manual"): hides spinboxes; shows a list of placed tab
    positions.  Selecting a list row highlights the corresponding canvas marker.
    Pressing Delete removes the selected tab.
    """

    tab_selected         = Signal(int)   # list row selected (-1 = none)
    tab_delete_requested = Signal(int)   # delete the tab at this index
    tab_list_hovered     = Signal(int)   # row index mouse entered in tab list

    def __init__(self, model: ConfigModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._widgets: dict[str, QWidget] = {}
        self._refreshing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # Type selector (always visible)
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Tab type"))
        self._type_combo = QComboBox()
        for f in TABS_FIELDS:
            if f.key == "type":
                for c in f.choices:
                    self._type_combo.addItem(c)
                break
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_combo, 1)
        outer.addLayout(type_row)

        # Standard section (all non-manual types)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._standard_inner = QWidget()
        form = QFormLayout(self._standard_inner)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setContentsMargins(0, 4, 0, 0)
        form.setSpacing(4)

        for key in _STANDARD_KEYS:
            field = _FIELD_BY_KEY[key]
            widget = self._make_widget(field)
            self._widgets[key] = widget
            label = QLabel(field.label)
            if field.tooltip:
                label.setToolTip(field.tooltip)
                widget.setToolTip(field.tooltip)
            form.addRow(label, widget)

        scroll.setWidget(self._standard_inner)
        self._scroll = scroll
        outer.addWidget(self._scroll, 1)

        # Manual section
        self._manual_section = QWidget()
        manual_layout = QVBoxLayout(self._manual_section)
        manual_layout.setContentsMargins(0, 4, 0, 0)
        manual_layout.setSpacing(4)

        manual_form = QFormLayout()
        manual_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        manual_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        manual_form.setSpacing(4)
        hwidth_field = _FIELD_BY_KEY["hwidth"]
        self._manual_hwidth = self._make_widget(hwidth_field)
        hwidth_label = QLabel(hwidth_field.label)
        if hwidth_field.tooltip:
            hwidth_label.setToolTip(hwidth_field.tooltip)
            self._manual_hwidth.setToolTip(hwidth_field.tooltip)
        manual_form.addRow(hwidth_label, self._manual_hwidth)
        manual_layout.addLayout(manual_form)

        hint = QLabel("Click on the board outline in the canvas to place tabs.\n"
                      "Select a tab and press Delete to remove it.")
        hint.setWordWrap(True)
        hint.setStyleSheet("QLabel { color: #aaaaaa; font-style: italic; }")
        from PySide6.QtWidgets import QSizePolicy as _SP
        sp = hint.sizePolicy()
        sp.setVerticalPolicy(_SP.Policy.Fixed)
        hint.setSizePolicy(sp)
        manual_layout.addWidget(hint)

        self._tab_list = _TabListWidget()
        _row_h = self._tab_list.fontMetrics().height() + 4  # approximate item row height
        self._tab_list.setMinimumHeight(_row_h * 10 + 2 * self._tab_list.frameWidth())
        self._tab_list.setMouseTracking(True)
        self._tab_list.viewport().setMouseTracking(True)
        self._tab_list.entered.connect(lambda idx: self.tab_list_hovered.emit(idx.row()))
        self._tab_list.currentRowChanged.connect(self._on_list_row_changed)
        self._tab_list.delete_requested.connect(self._on_list_delete)
        manual_layout.addWidget(self._tab_list, 1)

        clear_btn = QPushButton("Clear All Tabs")
        clear_btn.clicked.connect(self._clear_all_tabs)
        manual_layout.addWidget(clear_btn)
        manual_layout.addStretch()

        outer.addWidget(self._manual_section, 1)

        model.config_changed.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------
    # Widget factory
    # ------------------------------------------------------------------

    def _make_widget(self, field) -> QWidget:
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
        return QLabel(f"[{field.key}]")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_type_changed(self, tab_type: str) -> None:
        if not self._refreshing:
            self._model.set("tabs", "type", tab_type)
        is_manual = tab_type == "manual"
        self._scroll.setVisible(not is_manual)
        self._manual_section.setVisible(is_manual)

    def _on_change(self, key: str, value: Any) -> None:
        if not self._refreshing:
            self._model.set("tabs", key, value)

    def _on_list_row_changed(self, row: int) -> None:
        if not self._refreshing:
            self.tab_selected.emit(row)

    def _on_list_delete(self) -> None:
        row = self._tab_list.currentRow()
        if row >= 0:
            self.tab_delete_requested.emit(row)

    def _clear_all_tabs(self) -> None:
        self._model.set("tabs", "positions", [])

    def highlight_tab_row(self, row: int | None) -> None:
        """Scroll to and visually indicate a tab row (called on canvas marker hover)."""
        if row is None or row >= self._tab_list.count():
            return
        item = self._tab_list.item(row)
        if item:
            self._tab_list.scrollToItem(item)
            self._refreshing = True
            try:
                self._tab_list.setCurrentRow(row)
            finally:
                self._refreshing = False

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self._refreshing = True
        try:
            tab_type = str(self._model.get("tabs", "type"))

            idx = self._type_combo.findText(tab_type)
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)

            is_manual = tab_type == "manual"
            self._scroll.setVisible(not is_manual)
            self._manual_section.setVisible(is_manual)

            for key, widget in self._widgets.items():
                try:
                    value = self._model.get("tabs", key)
                except KeyError:
                    continue
                _set_widget_value(widget, value)

            try:
                _set_widget_value(self._manual_hwidth, self._model.get("tabs", "hwidth"))
            except KeyError:
                pass

            try:
                positions = self._model.get("tabs", "positions") or []
            except KeyError:
                positions = []
            self._tab_list.clear()
            for i, pos in enumerate(positions):
                x = pos.get("x", 0.0)
                y = pos.get("y", 0.0)
                a = pos.get("a", 0.0)
                self._tab_list.addItem(f"Tab {i + 1}: ({x:.2f}, {y:.2f})  {a:.1f}°")

        except KeyError:
            pass
        finally:
            self._refreshing = False
