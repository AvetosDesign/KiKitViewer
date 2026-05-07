from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, QItemSelectionModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.config.schema import LAYOUT_FIELDS

# Fields shown in the grid sub-section (in order)
_GRID_KEYS = ["rows", "cols", "hspace", "vspace", "rotation", "alternation"]

# Build a lookup by key for easy access to Field metadata
_FIELD_BY_KEY = {f.key: f for f in LAYOUT_FIELDS}


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
            key == Qt.Key.Key_Tab
            and bool(mods & Qt.KeyboardModifier.ShiftModifier)
        )
        forward = key == Qt.Key.Key_Tab and not bool(
            mods & Qt.KeyboardModifier.ShiftModifier
        )
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
            QTimer.singleShot(0, lambda: (
                self._table.setCurrentCell(row, next_col),
                self._table.editItem(self._table.item(row, next_col)),
            ))
            return True

        return super().eventFilter(editor, event)


class LayoutPanel(QWidget):
    """
    Custom layout parameter panel supporting both grid and table layout types.

    Grid mode: rows/cols/spacing/rotation controls (standard KiKit grid layout).
    Table mode: QTableWidget of explicit (X mm, Y mm, Rotation °) board positions
                translated to KiKit's plugin API at run time.
    """

    board_highlighted   = Signal(str, float, float, float, float, float)  # svg, x, y, w, h, rotation
    boards_highlighted  = Signal(list)   # list of (row, cx, cy, w, h, rot, svg, is_selected) tuples
    board_deselected    = Signal()
    board_hovered       = Signal(int)   # table row index
    board_hover_cleared = Signal()

    def __init__(self, model: ConfigModel, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self._refreshing = False
        self._grid_widgets: dict[str, QWidget] = {}
        self._board_size: tuple[float, float] | None = None
        self._edge_cuts_svg: str | None = None
        self._panel_ox: float = 0.0
        self._panel_oy: float = 0.0
        self._last_selected_row: int | None = None
        model.board_path_changed.connect(lambda _: self._invalidate_board_size())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # -- Type selector (always visible) ----------------------------------
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Layout mode"))
        self._type_combo = QComboBox()
        self._type_combo.addItems(["grid", "manual"])
        self._type_combo.currentTextChanged.connect(self._on_type_changed)
        type_row.addWidget(self._type_combo, 1)
        outer.addLayout(type_row)

        # -- Grid section ----------------------------------------------------
        self._grid_section = QWidget()
        grid_form = QFormLayout(self._grid_section)
        grid_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        grid_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        grid_form.setContentsMargins(0, 0, 0, 0)
        grid_form.setSpacing(4)

        for key in _GRID_KEYS:
            field = _FIELD_BY_KEY[key]
            widget = self._make_widget(field)
            self._grid_widgets[key] = widget
            label = QLabel(field.label)
            if field.tooltip:
                label.setToolTip(field.tooltip)
                widget.setToolTip(field.tooltip)
            grid_form.addRow(label, widget)

        outer.addWidget(self._grid_section)

        # -- Table section ---------------------------------------------------
        self._table_section = QWidget()
        table_layout = QVBoxLayout(self._table_section)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(4)

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
        self._table.entered.connect(lambda idx: self.board_hovered.emit(idx.row()))
        self._table.viewport().installEventFilter(self)
        self._table.setStyleSheet(
            "QTableWidget::item:hover { background: rgba(255,255,255,25); }"
            "QTableWidget::item:focus { background: rgba(255,255,255,20); outline: none; }"
        )
        table_layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Board")
        add_btn.clicked.connect(self._add_row)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected_rows)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        table_layout.addLayout(btn_row)

        outer.addWidget(self._table_section)
        outer.addStretch()

        model.config_changed.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------
    # Widget factory (grid fields only)
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
        # fallback (shouldn't be reached for grid keys)
        return QLabel(f"[{field.key}]")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_type_changed(self, layout_type: str) -> None:
        if not self._refreshing:
            self._model.set("layout", "type", layout_type)
            if layout_type == "manual":
                existing = self._model.get("layout", "positions") or []
                if not existing:
                    self._seed_table_from_grid()
            else:
                self.board_deselected.emit()
        self._grid_section.setVisible(layout_type == "grid")
        self._table_section.setVisible(layout_type == "manual")

    def _on_grid_change(self, key: str, value: Any) -> None:
        if not self._refreshing:
            self._model.set("layout", key, value)

    def _add_row(self) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        for col in range(3):
            self._table.setItem(row, col, QTableWidgetItem("0.0"))
        # itemChanged fires automatically, which calls _on_table_changed

    def _remove_selected_rows(self) -> None:
        rows = sorted(
            {idx.row() for idx in self._table.selectedIndexes()}, reverse=True
        )
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
        self._on_selection_changed()

    def set_board_geometry(self, w: float, h: float, edge_cuts_svg: str = "") -> None:
        """Cache board dimensions and Edge_Cuts SVG returned by the panel worker."""
        self._board_size = (w, h)
        self._edge_cuts_svg = edge_cuts_svg or None

    def _invalidate_board_size(self) -> None:
        self._board_size = None
        self._edge_cuts_svg = None

    def _get_board_size(self) -> tuple[float, float] | None:
        if self._board_size is not None:
            return self._board_size
        board_path = self._model.board_path
        if board_path is None:
            return None
        try:
            import pcbnew  # type: ignore[import]
            board = pcbnew.LoadBoard(str(board_path))
            bbox = board.GetBoardEdgesBoundingBox()
            self._board_size = (
                pcbnew.ToMM(bbox.GetWidth()),
                pcbnew.ToMM(bbox.GetHeight()),
            )
            return self._board_size
        except Exception:
            return None

    def _get_edge_cuts_svg(self) -> str | None:
        if self._edge_cuts_svg is not None:
            return self._edge_cuts_svg
        board_path = self._model.board_path
        if board_path is None:
            return None
        try:
            from kikit_viewer.renderer.pcbnew_renderer import PcbnewSvgRenderer
            layers = PcbnewSvgRenderer().render_layers(board_path, ["Edge_Cuts"])
            svg = layers.get("Edge_Cuts", "")
            self._edge_cuts_svg = svg or None
            return self._edge_cuts_svg
        except Exception:
            return None

    def eventFilter(self, obj, event) -> bool:
        if obj is self._table.viewport() and event.type() == QEvent.Type.Leave:
            self.board_hover_cleared.emit()
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Public board geometry API (used by MainWindow for hover hit-testing)
    # ------------------------------------------------------------------

    @property
    def active_row(self) -> int | None:
        return self._last_selected_row

    @property
    def board_count(self) -> int:
        return self._table.rowCount()

    @property
    def selected_rows(self) -> list[int]:
        return sorted({idx.row() for idx in self._table.selectionModel().selectedRows()})

    @property
    def board_size(self) -> tuple[float, float] | None:
        return self._get_board_size()

    @property
    def edge_cuts_svg(self) -> str | None:
        return self._get_edge_cuts_svg()

    def panel_origin(self) -> tuple[float, float] | None:
        """Return (panel_ox, panel_oy) — public alias for use by MainWindow."""
        return self._compute_panel_origin()

    def select_row(self, row: int) -> None:
        """Programmatically select a single row, clearing other selections."""
        if 0 <= row < self._table.rowCount():
            self._table.selectRow(row)

    def set_selected_rows(self, rows: list[int]) -> None:
        """Programmatically select multiple rows without clearing all at once."""
        sm = self._table.selectionModel()
        sm.clearSelection()
        for row in rows:
            if 0 <= row < self._table.rowCount():
                sm.select(
                    self._table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select
                    | QItemSelectionModel.SelectionFlag.Rows,
                )

    def set_canvas_hover_row(self, row: int | None) -> None:
        """Called by MainWindow to show a focus indicator for the canvas-hovered row."""
        if row is None:
            self._table.clearFocus()
            return
        idx = self._table.model().index(row, 0)
        self._table.scrollTo(idx)
        self._table.selectionModel().setCurrentIndex(
            idx, QItemSelectionModel.SelectionFlag.NoUpdate
        )

    def _compute_panel_origin(self) -> tuple[float, float] | None:
        """Return (panel_ox, panel_oy) from board positions + framing, or None if no board."""
        size = self._get_board_size()
        if size is None:
            return None
        w, h = size
        try:
            positions = self._model.get("layout", "positions") or []
        except KeyError:
            positions = []
        min_x = min((float(p.get("x", 0.0)) for p in positions), default=0.0)
        min_y = min((float(p.get("y", 0.0)) for p in positions), default=0.0)
        try:
            frame_type   = str(self._model.get("framing", "type"))
            frame_width  = float(self._model.get("framing", "width"))
            frame_hspace = float(self._model.get("framing", "hspace"))
            frame_vspace = float(self._model.get("framing", "vspace"))
        except KeyError:
            frame_type, frame_width, frame_hspace, frame_vspace = "none", 0.0, 0.0, 0.0
        if frame_type in ("frame", "tightframe"):
            ox = min_x - w / 2.0 - frame_hspace - frame_width
            oy = min_y - h / 2.0 - frame_vspace - frame_width
        elif frame_type == "railstb":
            ox = min_x - w / 2.0
            oy = min_y - h / 2.0 - frame_vspace - frame_width
        elif frame_type == "railslr":
            ox = min_x - w / 2.0 - frame_hspace - frame_width
            oy = min_y - h / 2.0
        else:
            ox = min_x - w / 2.0
            oy = min_y - h / 2.0
        return ox, oy

    def board_scene_data(self, row: int) -> tuple[float, float, float, float, float, str] | None:
        """Return (scene_cx, scene_cy, w_mm, h_mm, rotation_deg, svg) for row, or None."""
        if row < 0 or row >= self._table.rowCount():
            return None
        origin = self._compute_panel_origin()
        if origin is None:
            return None
        ox, oy = origin
        size = self._get_board_size()
        if size is None:
            return None
        w, h = size

        def _cell(col: int) -> float:
            item = self._table.item(row, col)
            try:
                return float(item.text()) if item else 0.0
            except ValueError:
                return 0.0

        return _cell(0) - ox, _cell(1) - oy, w, h, _cell(2), self._get_edge_cuts_svg() or ""

    # ------------------------------------------------------------------

    def _on_selection_changed(self) -> None:
        if self._refreshing:
            return
        selected_rows = {idx.row() for idx in self._table.selectedIndexes()}
        rows = sorted(selected_rows)

        if rows:
            self._last_selected_row = rows[0]

        if not rows:
            self.board_deselected.emit()
            return

        # Update panel origin cache
        origin = self._compute_panel_origin()
        if origin:
            self._panel_ox, self._panel_oy = origin

        # Build overlay list for all boards (used by MainWindow for set_board_overlays)
        overlays = []
        for row in range(self._table.rowCount()):
            data = self.board_scene_data(row)
            if data is None:
                continue
            cx, cy, w, h, rot, svg = data
            overlays.append((row, cx, cy, w, h, rot, svg, row in selected_rows))
        self.boards_highlighted.emit(overlays)

        # Also emit legacy single-board signal (for grid-mode tabs path in highlight_first_board)
        row = rows[0]
        data = self.board_scene_data(row)
        if data is not None:
            cx, cy, w, h, rot, svg = data
            self.board_highlighted.emit(svg, cx, cy, w, h, rot)

    def _seed_table_from_grid(self) -> None:
        """Pre-populate table positions by computing the current grid layout."""
        size = self._get_board_size()
        if size is None:
            return
        board_w, board_h = size

        try:
            rows = int(self._model.get("layout", "rows"))
            cols = int(self._model.get("layout", "cols"))
            hspace = float(self._model.get("layout", "hspace"))
            vspace = float(self._model.get("layout", "vspace"))
            rotation = float(self._model.get("layout", "rotation"))
            alternation = str(self._model.get("layout", "alternation"))
        except KeyError:
            return

        positions = []
        for r in range(rows):
            for c in range(cols):
                # Mirror alternating rows/cols by adding 180° — matches KiKit's
                # alternation logic ("rows", "cols", "rowsCols").
                flip_row = (r % 2 == 1) and alternation in ("rows", "rowsCols")
                flip_col = (c % 2 == 1) and alternation in ("cols", "rowsCols")
                extra = 180.0 if (flip_row ^ flip_col) else 0.0
                positions.append({
                    "x": round(c * (board_w + hspace), 3),
                    "y": round(r * (board_h + vspace), 3),
                    "rotation": round(rotation + extra, 3),
                })
        self._model.set("layout", "positions", positions)

    def highlight_first_board(self) -> None:
        """Emit board_highlighted for the first board.

        In manual layout mode, delegates to selectRow(0) so that
        _last_selected_row stays in sync and _refresh() restores the correct
        highlight after any subsequent config_changed.

        In grid mode, computes first-board scene coords directly (no table row).
        """
        try:
            layout_type = str(self._model.get("layout", "type"))
        except KeyError:
            layout_type = "grid"

        if layout_type == "manual":
            if self._table.rowCount() > 0:
                self._table.selectRow(0)
            return

        size = self._get_board_size()
        if size is None:
            return
        w, h = size

        try:
            rotation = float(self._model.get("layout", "rotation"))
        except (KeyError, TypeError, ValueError):
            rotation = 0.0

        try:
            frame_type   = str(self._model.get("framing", "type"))
            frame_width  = float(self._model.get("framing", "width"))
            frame_hspace = float(self._model.get("framing", "hspace"))
            frame_vspace = float(self._model.get("framing", "vspace"))
        except KeyError:
            frame_type, frame_width, frame_hspace, frame_vspace = "none", 0.0, 0.0, 0.0

        if frame_type in ("frame", "tightframe"):
            panel_ox = -w / 2.0 - frame_hspace - frame_width
            panel_oy = -h / 2.0 - frame_vspace - frame_width
        elif frame_type == "railstb":
            panel_ox = -w / 2.0
            panel_oy = -h / 2.0 - frame_vspace - frame_width
        elif frame_type == "railslr":
            panel_ox = -w / 2.0 - frame_hspace - frame_width
            panel_oy = -h / 2.0
        else:
            panel_ox = -w / 2.0
            panel_oy = -h / 2.0

        self._panel_ox = panel_ox
        self._panel_oy = panel_oy
        svg = self._get_edge_cuts_svg() or ""
        self.board_highlighted.emit(svg, -panel_ox, -panel_oy, w, h, rotation)

    def restore_highlight(self) -> None:
        """Re-emit board_highlighted for the currently selected row after a scene reload."""
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if rows:
            self._on_selection_changed()

    def apply_board_drop(self, scene_cx: float, scene_cy: float) -> None:
        """Update the selected table row from a drag-drop on the canvas highlight (single-board path)."""
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()})
        if not rows:
            return
        row = rows[0]
        new_x = round(scene_cx + self._panel_ox, 3)
        new_y = round(scene_cy + self._panel_oy, 3)
        self._refreshing = True
        try:
            ix = self._table.item(row, 0)
            iy = self._table.item(row, 1)
            if ix:
                ix.setText(str(new_x))
            if iy:
                iy.setText(str(new_y))
        finally:
            self._refreshing = False
        self._on_table_changed()
        self._table.selectRow(row)

    def apply_multi_board_drop(self, moves: dict) -> None:
        """Update multiple table rows from a multi-board drag on the canvas.

        moves: dict mapping row → (new_scene_cx, new_scene_cy)
        """
        origin = self._compute_panel_origin()
        if origin is None:
            return
        ox, oy = origin
        moved_rows = sorted(moves.keys())
        self._refreshing = True
        try:
            for row, (scene_cx, scene_cy) in moves.items():
                new_x = round(scene_cx + ox, 3)
                new_y = round(scene_cy + oy, 3)
                ix = self._table.item(row, 0)
                iy = self._table.item(row, 1)
                if ix:
                    ix.setText(str(new_x))
                if iy:
                    iy.setText(str(new_y))
        finally:
            self._refreshing = False
        self._on_table_changed()
        # Restore selection so overlays are refreshed for all moved boards
        self.set_selected_rows(moved_rows)

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        self.board_deselected.emit()
        self._refreshing = True
        try:
            layout_type = str(self._model.get("layout", "type"))

            # Type combo
            idx = self._type_combo.findText(layout_type)
            if idx >= 0:
                self._type_combo.setCurrentIndex(idx)

            # Show/hide sections
            self._grid_section.setVisible(layout_type == "grid")
            self._table_section.setVisible(layout_type == "manual")

            # Grid widgets
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

            # Table
            try:
                positions = self._model.get("layout", "positions") or []
            except KeyError:
                positions = []

            self._table.setRowCount(0)
            for pos in positions:
                row = self._table.rowCount()
                self._table.insertRow(row)
                self._table.setItem(row, 0, QTableWidgetItem(str(pos.get("x", 0.0))))
                self._table.setItem(row, 1, QTableWidgetItem(str(pos.get("y", 0.0))))
                self._table.setItem(row, 2, QTableWidgetItem(str(pos.get("rotation", 0.0))))

        finally:
            self._refreshing = False

        # Restore selection so highlight stays visible after a config_changed
        # triggered by something other than the user (e.g., tab placement).
        if self._last_selected_row is not None:
            n = self._table.rowCount()
            row_to_select = min(self._last_selected_row, n - 1)
            if row_to_select >= 0:
                self._table.selectRow(row_to_select)
