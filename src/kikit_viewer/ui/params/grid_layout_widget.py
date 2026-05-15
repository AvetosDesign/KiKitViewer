from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.config.schema import LAYOUT_FIELDS
from kikit_viewer.ui.canvas.board_overlay_item import BoardSceneData
from kikit_viewer.ui.params._layout_geometry import panel_origin as _panel_origin

# Fields shown in the grid form (in order)
_GRID_KEYS = ["rows", "cols", "vspace", "hspace", "rotation", "alternation"]

_FIELD_BY_KEY = {f.key: f for f in LAYOUT_FIELDS}


class GridLayoutWidget(QWidget):
    """
    Form-based UI for KiKit's built-in grid layout mode.

    Exposes the same generalised API as TableLayoutWidget so LayoutPanel can
    dispatch to either widget uniformly.  Index semantics use raster-scan order:
        index = row * cols + col
    """

    board_highlighted = Signal(object)  # BoardSceneData
    positions_changed = Signal()
    selection_changed = Signal(list)  # list[int] raster-scan indices
    hovered = Signal(int)  # declared for API parity; never emitted
    hover_cleared = Signal()  # declared for API parity; never emitted

    def __init__(self, model: ConfigModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._refreshing = False
        self._selected: set[int] = set()
        self._board_size: tuple[float, float] | None = None
        self._edge_cuts_svg: str | None = None
        self._grid_widgets: dict[str, QWidget] = {}

        form = QFormLayout(self)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(4)

        for key in _GRID_KEYS:
            field = _FIELD_BY_KEY[key]
            widget = self._make_widget(field)
            self._grid_widgets[key] = widget
            label = QLabel(field.label)
            if field.tooltip:
                label.setToolTip(field.tooltip)
                widget.setToolTip(field.tooltip)
            form.addRow(label, widget)

    # ------------------------------------------------------------------
    # Widget factory
    # ------------------------------------------------------------------

    def _make_widget(self, field) -> QWidget:
        if field.type == "choice":
            w = QComboBox()
            for c in field.choices:
                w.addItem(c)
            w.currentTextChanged.connect(lambda v, k=field.key: self._on_grid_change(k, v))
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
            w.editingFinished.connect(lambda k=field.key, w=w: self._on_grid_change(k, w.value()))
            return w
        if field.type == "int":
            w = QSpinBox()
            w.setRange(
                int(field.min_val) if field.min_val is not None else 0,
                int(field.max_val) if field.max_val is not None else 9999,
            )
            if field.unit:
                w.setSuffix(f" {field.unit}")
            w.editingFinished.connect(lambda k=field.key, w=w: self._on_grid_change(k, w.value()))
            return w
        return QLabel(f"[{field.key}]")

    # ------------------------------------------------------------------
    # Board geometry
    # ------------------------------------------------------------------

    def set_board_geometry(self, w: float, h: float, svg: str = "") -> None:
        self._board_size = (w, h)
        self._edge_cuts_svg = svg or None

    def _grid_positions(self) -> list[dict]:
        """Compute board positions from current model params (read-only)."""
        if self._board_size is None:
            return []
        bw, bh = self._board_size
        try:
            rows = int(self._model.get("layout", "rows"))
            cols = int(self._model.get("layout", "cols"))
            hspace = float(self._model.get("layout", "hspace"))
            vspace = float(self._model.get("layout", "vspace"))
            rotation = float(self._model.get("layout", "rotation"))
            alternation = str(self._model.get("layout", "alternation"))
        except KeyError:
            return []

        positions = []
        for r in range(rows):
            for c in range(cols):
                flip_row = (r % 2 == 1) and alternation in ("rows", "rowsCols")
                flip_col = (c % 2 == 1) and alternation in ("cols", "rowsCols")
                extra = 180.0 if (flip_row ^ flip_col) else 0.0
                positions.append(
                    {
                        "x": round(c * (bw + hspace), 3),
                        "y": round(r * (bh + vspace), 3),
                        "rotation": round(rotation + extra, 3),
                    }
                )
        return positions

    def panel_origin(self) -> tuple[float, float] | None:
        if self._board_size is None:
            return None
        bw, bh = self._board_size
        positions = self._grid_positions()
        if not positions:
            return None
        return _panel_origin(self._model, positions, bw, bh)

    def board_scene_data(self, index: int) -> BoardSceneData | None:
        """Return (scene_cx, scene_cy, w_mm, h_mm, rotation_deg, svg), or None."""
        pos_data = self.get(index)
        if pos_data is None:
            return None
        origin = self.panel_origin()
        if origin is None:
            return None
        if self._board_size is None:
            return None
        ox, oy = origin
        bw, bh = self._board_size
        x, y, rot = pos_data
        return (x - ox, y - oy, bw, bh, rot, self._edge_cuts_svg or "")

    # ------------------------------------------------------------------
    # Public API (mirrors TableLayoutWidget)
    # ------------------------------------------------------------------

    @property
    def active(self) -> int:
        """Always returns 0 (board at raster index 0, i.e. row=0, col=0)."""
        return 0

    @property
    def board_count(self) -> int:
        """Total boards in the grid (rows × cols)."""
        positions = self._grid_positions()
        return len(positions)

    @property
    def selected(self) -> list[int]:
        """Sorted raster-scan indices of selected boards."""
        return sorted(self._selected)

    def select(self, index: int) -> None:
        """Set the nth raster-scan board as the sole selection."""
        if 0 <= index < self.board_count:
            self._selected = {index}
            self.selection_changed.emit(self.selected)

    def set_selected(self, indexes: list[int]) -> None:
        """Set selection by raster-scan indexes."""
        count = self.board_count
        self._selected = {i for i in indexes if 0 <= i < count}
        self.selection_changed.emit(self.selected)

    def set_canvas_hover(self, index: int | None) -> None:
        """No-op — grid mode has no per-board hover widget."""

    def get(self, index: int) -> tuple[float, float, float] | None:
        """Return (x, y, rotation) for the nth raster-scan board, or None."""
        positions = self._grid_positions()
        if index < 0 or index >= len(positions):
            return None
        p = positions[index]
        return float(p["x"]), float(p["y"]), float(p["rotation"])

    def refresh(self) -> None:
        """Update form widgets from model and emit selection_changed."""
        self._refreshing = True
        try:
            for key, widget in self._grid_widgets.items():
                try:
                    value = self._model.get("layout", key)
                except KeyError:
                    continue
                if isinstance(widget, QComboBox):
                    idx = widget.findText(str(value))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)
                elif isinstance(widget, QDoubleSpinBox):
                    widget.setValue(float(value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(value))
        finally:
            self._refreshing = False
        # Always emit so LayoutPanel can push overlay updates
        self.selection_changed.emit(self.selected)

    def apply_board_drop(self, moves: dict, panel_orig: tuple[float, float]) -> None:
        """No-op — grid positions are computed, not draggable."""

    def restore_highlight(self) -> None:
        self._emit_board_highlighted()

    def highlight_first_board(self) -> None:
        self._emit_board_highlighted()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _on_grid_change(self, key: str, value: Any) -> None:
        if not self._refreshing:
            self._model.set("layout", key, value)
            self.positions_changed.emit()

    def _emit_board_highlighted(self) -> None:
        """Emit board_highlighted for the first board (index 0)."""
        if self._board_size is None:
            return
        bw, bh = self._board_size

        try:
            rotation = float(self._model.get("layout", "rotation"))
        except (KeyError, TypeError, ValueError):
            rotation = 0.0

        data = self.board_scene_data(0)
        if data is None:
            return
        self.board_highlighted.emit(data)
