from __future__ import annotations

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.ui.params._layout_geometry import panel_origin as _panel_origin


class _RowCycleDelegate(QStyledItemDelegate):
    """
    Item delegate for the positions table.

    Enter  — commits the edit and re-selects the current row so the board
             highlight stays visible (default behaviour advances to next row).
    Tab    — commits and moves to the next column in the same row, wrapping.
    Shift+Tab — same, backwards.
    """

    def __init__(self, table: QTableWidget) -> None:
        super().__init__(table)
        self._table = table

    def eventFilter(self, editor, event) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return super().eventFilter(editor, event)

        key = event.key()
        mods = event.modifiers()
        backward = key == Qt.Key.Key_Backtab or (
            key == Qt.Key.Key_Tab and bool(mods & Qt.KeyboardModifier.ShiftModifier)
        )
        forward = key == Qt.Key.Key_Tab and not bool(mods & Qt.KeyboardModifier.ShiftModifier)
        enter = key in (Qt.Key.Key_Return, Qt.Key.Key_Enter)

        if enter:
            row = self._table.currentRow()
            self.commitData.emit(editor)
            self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
            # Deferred re-select: model update cascade (_refresh) rebuilds the
            # table synchronously, clearing selection, before we return here.
            QTimer.singleShot(0, lambda: self._table.selectRow(row))
            return True

        if forward or backward:
            row = self._table.currentRow()
            col = self._table.currentColumn()
            next_col = (col + (-1 if backward else 1)) % self._table.columnCount()
            self.commitData.emit(editor)
            self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)
            QTimer.singleShot(
                0,
                lambda: (
                    self._table.setCurrentCell(row, next_col),
                    self._table.editItem(self._table.item(row, next_col)),
                ),
            )
            return True

        return super().eventFilter(editor, event)


class TableLayoutWidget(QWidget):
    """
    Owner of the manual-layout positions QTableWidget.

    Reads and writes layout.positions in the model; exposes selection state and
    per-board geometry to LayoutPanel.
    """

    positions_changed = Signal()
    selection_changed = Signal(list)   # list[int] of selected indices
    hovered = Signal(int)              # mouse entered a row
    hover_cleared = Signal()           # mouse left the viewport

    def __init__(self, model: ConfigModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._refreshing = False
        self._last_selected: int | None = None
        self._board_size: tuple[float, float] | None = None
        self._edge_cuts_svg: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["X (mm)", "Y (mm)", "Rotation (°)"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setItemDelegate(_RowCycleDelegate(self._table))
        self._table.itemChanged.connect(self._on_table_changed)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.setMouseTracking(True)
        self._table.viewport().setMouseTracking(True)
        self._table.entered.connect(lambda idx: self.hovered.emit(idx.row()))
        self._table.viewport().installEventFilter(self)
        self._table.setStyleSheet(
            "QTableWidget::item:hover { background: rgba(255,255,255,25); }"
            "QTableWidget::item:focus { background: rgba(255,255,255,20); outline: none; }"
        )
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Board")
        add_btn.clicked.connect(self._add_row)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected_rows)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        layout.addLayout(btn_row)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._table.viewport() and event.type() == QEvent.Type.Leave:
            self.hover_cleared.emit()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Board geometry
    # ------------------------------------------------------------------

    def set_board_geometry(self, w: float, h: float, svg: str = "") -> None:
        self._board_size = (w, h)
        self._edge_cuts_svg = svg or None

    def panel_origin(self) -> tuple[float, float] | None:
        if self._board_size is None:
            return None
        w, h = self._board_size
        try:
            positions = self._model.get("layout", "positions") or []
        except KeyError:
            positions = []
        return _panel_origin(self._model, positions, w, h)

    def board_scene_data(self, index: int) -> tuple[float, float, float, float, float, str] | None:
        """Return (scene_cx, scene_cy, w_mm, h_mm, rotation_deg, svg), or None."""
        row_data = self.get(index)
        if row_data is None:
            return None
        origin = self.panel_origin()
        if origin is None:
            return None
        if self._board_size is None:
            return None
        ox, oy = origin
        w, h = self._board_size
        x, y, rot = row_data
        return x - ox, y - oy, w, h, rot, self._edge_cuts_svg or ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def active(self) -> int | None:
        return self._last_selected

    @property
    def board_count(self) -> int:
        return self._table.rowCount()

    @property
    def selected(self) -> list[int]:
        return sorted({idx.row() for idx in self._table.selectionModel().selectedRows()})

    def select(self, index: int) -> None:
        if 0 <= index < self._table.rowCount():
            self._table.selectRow(index)

    def set_selected(self, indexes: list[int]) -> None:
        sm = self._table.selectionModel()
        sm.clearSelection()
        for index in indexes:
            if 0 <= index < self._table.rowCount():
                sm.select(
                    self._table.model().index(index, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    def set_canvas_hover(self, index: int | None) -> None:
        if index is None:
            self._table.clearFocus()
            return
        idx = self._table.model().index(index, 0)
        self._table.scrollTo(idx)
        self._table.selectionModel().setCurrentIndex(
            idx, QItemSelectionModel.SelectionFlag.NoUpdate
        )

    def get(self, index: int) -> tuple[float, float, float] | None:
        """Return (x, y, rotation) for the given index, or None if out of range."""
        if index < 0 or index >= self._table.rowCount():
            return None

        def _cell(col: int) -> float:
            item = self._table.item(index, col)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0

        return _cell(0), _cell(1), _cell(2)

    def refresh(self, positions: list) -> bool:
        """
        Repopulate the table from a positions list.

        Returns True if a prior selection was restored (causing selection_changed to
        fire via selectRow), False if the caller should push a fresh overlay update.
        """
        self._refreshing = True
        try:
            self._table.setRowCount(0)
            for pos in positions:
                r = self._table.rowCount()
                self._table.insertRow(r)
                self._table.setItem(r, 0, QTableWidgetItem(str(pos.get("x", 0.0))))
                self._table.setItem(r, 1, QTableWidgetItem(str(pos.get("y", 0.0))))
                self._table.setItem(r, 2, QTableWidgetItem(str(pos.get("rotation", 0.0))))
        finally:
            self._refreshing = False

        if self._last_selected is not None:
            n = self._table.rowCount()
            row_to_select = min(self._last_selected, n - 1)
            if row_to_select >= 0:
                self._table.selectRow(row_to_select)
                return True

        return False

    def apply_board_drop(self, moves: dict, panel_orig: tuple[float, float]) -> None:
        """
        Update table cells from a canvas drag or rotation.

        moves: index → (scene_cx, scene_cy[, rotation_deg])
        panel_orig: (ox, oy) to convert scene coords back to absolute positions
        """
        ox, oy = panel_orig
        moved = sorted(moves.keys())
        self._refreshing = True
        try:
            for index, pos_data in moves.items():
                scene_cx, scene_cy = pos_data[0], pos_data[1]
                new_x = round(scene_cx + ox, 3)
                new_y = round(scene_cy + oy, 3)
                ix = self._table.item(index, 0)
                iy = self._table.item(index, 1)
                if ix:
                    ix.setText(str(new_x))
                if iy:
                    iy.setText(str(new_y))
                if len(pos_data) > 2:
                    ir = self._table.item(index, 2)
                    if ir:
                        ir.setText(str(round(pos_data[2] or 0.0, 3)))
        finally:
            self._refreshing = False
        self._on_table_changed()
        self.set_selected(moved)

    def restore_highlight(self) -> None:
        self._on_selection_changed()

    def highlight_first_board(self) -> None:
        if self._table.rowCount() > 0:
            self.select(0)

    # ------------------------------------------------------------------
    # Internal slots
    # ------------------------------------------------------------------

    def _add_row(self) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col in range(3):
            self._table.setItem(row, col, QTableWidgetItem("0.0"))
        # itemChanged fires automatically, triggering _on_table_changed

    def _remove_selected_rows(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True)
        for row in rows:
            self._table.removeRow(row)
        self._on_table_changed()

    def _on_table_changed(self) -> None:
        if self._refreshing:
            return

        def _cell(row: int, col: int) -> float:
            item = self._table.item(row, col)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0

        positions = [
            {"x": _cell(r, 0), "y": _cell(r, 1), "rotation": _cell(r, 2)}
            for r in range(self._table.rowCount())
        ]
        self._model.set("layout", "positions", positions)
        self.positions_changed.emit()

    def _on_selection_changed(self) -> None:
        if self._refreshing:
            return
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if rows:
            self._last_selected = rows[0]
        self.selection_changed.emit(rows)
