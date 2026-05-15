from __future__ import annotations

import math

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.ui.canvas.board_overlay_item import BoardSceneData
from kikit_viewer.ui.params.grid_layout_widget import GridLayoutWidget
from kikit_viewer.ui.params.table_layout_widget import TableLayoutWidget


class LayoutPanel(QWidget):
    """
    Thin coordinator for grid/manual layout modes.

    Owns the type selector and two child widgets (GridLayoutWidget,
    TableLayoutWidget).  All board-geometry and selection logic lives in
    the child widgets; this class forwards their signals and delegates every
    public method call to the currently-active widget.
    """

    board_highlighted = Signal(object)  # BoardSceneData
    boards_moved = Signal(list)
    boards_selected = Signal(list)
    layout_type_changed = Signal()
    board_hovered = Signal(int)
    board_hover_cleared = Signal()

    def __init__(self, model: ConfigModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._refreshing = False
        model.board_path_changed.connect(self._on_board_path_changed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # -- Type selector ---------------------------------------------------
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Layout mode"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(["grid", "manual"])
        self._type_combo.currentTextChanged.connect(self._on_layout_type_changed)
        type_row.addWidget(self._type_combo, 1)
        outer.addLayout(type_row)

        # -- Child widgets ---------------------------------------------------
        self._grid_widget = GridLayoutWidget(model)
        self._table_widget = TableLayoutWidget(model)
        outer.addWidget(self._grid_widget)
        outer.addWidget(self._table_widget)
        outer.addStretch()

        # -- Signal wiring ---------------------------------------------------
        for w in (self._grid_widget, self._table_widget):
            w.positions_changed.connect(self._on_widget_positions_changed)
            w.selection_changed.connect(self._on_widget_selection_changed)
            w.hovered.connect(self.board_hovered)
            w.hover_cleared.connect(self.board_hover_cleared)
        self._grid_widget.board_highlighted.connect(self.board_highlighted)

        model.config_changed.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------
    # Active widget dispatch
    # ------------------------------------------------------------------

    def _active_widget(self) -> GridLayoutWidget | TableLayoutWidget:
        try:
            if self._model.get("layout", "type") == "manual":
                return self._table_widget
        except KeyError:
            pass
        return self._grid_widget

    # ------------------------------------------------------------------
    # Public API — all delegated to the active widget
    # ------------------------------------------------------------------

    @property
    def active(self) -> int | None:
        return self._active_widget().active

    @property
    def board_count(self) -> int:
        return self._active_widget().board_count

    @property
    def selected(self) -> list[int]:
        return self._active_widget().selected

    def select(self, index: int) -> None:
        self._active_widget().select(index)

    def set_selected(self, indexes: list[int]) -> None:
        self._active_widget().set_selected(indexes)

    def set_canvas_hover(self, index: int | None) -> None:
        self._active_widget().set_canvas_hover(index)

    def apply_board_drop(self, moves: dict) -> None:
        origin = self._active_widget().panel_origin()
        if origin is not None:
            self._active_widget().apply_board_drop(moves, origin)

    def board_scene_data(self, index: int) -> BoardSceneData | None:
        return self._active_widget().board_scene_data(index)

    def panel_origin(self) -> tuple[float, float] | None:
        return self._active_widget().panel_origin()

    def restore_highlight(self) -> None:
        self._active_widget().restore_highlight()

    def highlight_first_board(self) -> None:
        self._active_widget().highlight_first_board()

    def set_board_geometry(self, w: float, h: float, edges_svg: str = "") -> None:
        """Push board size + SVG to both widgets; called by MainWindow after each run."""
        self._grid_widget.set_board_geometry(w, h, edges_svg)
        self._table_widget.set_board_geometry(w, h, edges_svg)

    @property
    def board_size(self) -> tuple[float, float] | None:
        return self._active_widget()._board_size

    @property
    def edge_cuts_svg(self) -> str | None:
        return self._active_widget()._edge_cuts_svg

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _on_board_path_changed(self, _) -> None:
        self._grid_widget.set_board_geometry(0.0, 0.0, "")
        self._table_widget.set_board_geometry(0.0, 0.0, "")
        # Clear board_size by setting to None sentinel
        self._grid_widget._board_size = None
        self._grid_widget._edge_cuts_svg = None
        self._table_widget._board_size = None
        self._table_widget._edge_cuts_svg = None

    def _on_layout_type_changed(self, layout_type: str) -> None:
        """Invoked when the layout type (grid vs. manual) changes."""
        if not self._refreshing:
            self._model.set("layout", "type", layout_type)
            if layout_type == "manual":
                existing = self._model.get("layout", "positions") or []
                if not existing:
                    self._seed_table_from_grid()
            else:
                self.layout_type_changed.emit()
        # Refresh which layout widget is visible
        self._grid_widget.setVisible(layout_type == "grid")
        self._table_widget.setVisible(layout_type == "manual")

    def _seed_table_from_grid(self) -> None:
        """Pre-populate manual positions from the current grid layout."""
        count = self._grid_widget.board_count
        positions = []
        for i in range(count):
            pos = self._grid_widget.get(i)
            if pos is not None:
                x, y, rot = pos
                positions.append({"x": x, "y": y, "rotation": rot})
        if positions:
            self._model.set("layout", "positions", positions)

    def _build_board_list(self, indexes: list[int]) -> list:
        """Construct a list of board scene data from a list of indexes."""
        boards = []
        for i in indexes:
            data = self._active_widget().board_scene_data(i)
            if data is not None:
                boards.append((i, data))
        return boards

    def _on_widget_positions_changed(self) -> None:
        # Only act when the signal comes from the active widget
        sender = self.sender()
        if sender is not self._active_widget():
            return
        boards = self._build_board_list(self._active_widget().selected)
        self.boards_moved.emit(boards)

    def _on_widget_selection_changed(self, indexes: list[int]) -> None:
        # Only act when the signal comes from the active panel widget
        sender = self.sender()
        if sender is not self._active_widget():
            return
        boards = self._build_board_list(indexes)
        self.boards_selected.emit(boards)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        """ Read the current layout type from the model """
        try:
            layout_type = str(self._model.get("layout", "type"))
        except KeyError:
            layout_type = "grid"

        self._refreshing = True
        try:
            # Select the current type in the combo list (if we can find it)
            idx = self._type_combo.findText(layout_type)
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)

            # Enable the associated form widget
            self._grid_widget.setVisible(layout_type == "grid")
            self._table_widget.setVisible(layout_type == "manual")
        finally:
            self._refreshing = False

        # Child widget refresh runs outside the _refreshing guard so that
        # selection-restoration signals propagate to _on_selection_changed.
        if layout_type == "manual":
            try:
                positions = self._model.get("layout", "positions") or []
            except KeyError:
                positions = []
            restored = self._table_widget.refresh(positions)
            if not restored:
                self._on_widget_selection_changed([])
        else:
            self._grid_widget.refresh()
