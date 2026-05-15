from __future__ import annotations

import math
import shutil
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QPointF, QSettings, QSize, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from kikit_viewer.config import serialization, translation
from kikit_viewer.config.model import ConfigModel
from kikit_viewer.runner.run_coordinator import RunCoordinator
from kikit_viewer.ui.canvas.board_overlay_item import BoardEntry, BoardOverlayItem, BoardSceneData
from kikit_viewer.ui.canvas.pcb_panel_view import PcbPanelView
from kikit_viewer.ui.canvas.scene import PanelScene
from kikit_viewer.ui.debug_dock import DebugGeometryDock
from kikit_viewer.ui.layers_panel import LayersPanel
from kikit_viewer.ui.params.cuts_panel import CutsPanel
from kikit_viewer.ui.params.framing_panel import FramingPanel
from kikit_viewer.ui.params.layout_panel import LayoutPanel
from kikit_viewer.ui.params.post_panel import PostPanel
from kikit_viewer.ui.params.tabs_panel import TabsPanel
from kikit_viewer.ui.params.text_panel import TextPanel

_MAX_RECENT = 8
_SETTINGS_ORG = "KiKitViewer"
_SETTINGS_APP = "KiKitViewer"
_OVERLAY_FILL_COLOR = QColor(0x39, 0xB4, 0xEA, 64)  # #3daee9 25% opacity
_OVERLAY_PEN_COLOR = QColor(0x39, 0xB4, 0xEA)


class _ToolButtonOverlay(QToolButton):
    """QToolButton that paints a semi-transparent colour overlay when checked."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self.isChecked():
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(_OVERLAY_PEN_COLOR)
            painter.setBrush(_OVERLAY_FILL_COLOR)
            painter.drawRect(self.rect())
            painter.end()


class MainWindow(QMainWindow):
    """
    Top-level application window.

    Layout:
      Left toolbar — vertical icon toolbar (Auto Refresh toggle, …)
      Centre       — PanelView (canvas showing rendered panel)
      Right dock   — QTabWidget with parameter panels

    The RunCoordinator lives here and connects the ConfigModel to the KiKit
    runner. On run_finished the canvas refreshes and the preview path is cached
    for use by Export.
    """

    opmode_changed = Signal(str)  # active param tab name

    def __init__(self, board_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KiKit Viewer")
        self.resize(1280, 800)

        self._model = ConfigModel(parent=self)
        self._coordinator = RunCoordinator(self._model, parent=self)
        self._last_preview: Path | None = None  # most recent successful panel output
        self._config_dirty: bool = False
        self._model.config_changed.connect(self._on_config_changed_overlay)
        self._model.undo_state_changed.connect(self._on_undo_state_changed)

        # Board outline — cached from run result, distributed to overlay items.
        self._board_outline = None  # Shapely LinearRing, same shape for all boards
        self._model.board_path_changed.connect(lambda _: self._clear_board_outline())

        # Per-board overlay items (board_id → BoardOverlayItem); cleared on each run.
        self._overlay_items: list[BoardOverlayItem] = []
        self._tab_target_id: int | None = None
        self._hover_board_idx: int | None = None

        # Clipboard + float-mode state for copy/paste
        self._board_clipboard: list[dict] = []
        self._float_mode: bool = False

        # Canvas
        self._scene = PanelScene()
        self._view = PcbPanelView()
        self._view.setScene(self._scene)
        self.setCentralWidget(self._view)

        # Parameter dock (left side, outside the toolbar)
        self._param_dock, self._param_dock_default_width = self._build_param_dock()
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self._param_dock)

        # Layers dock (right side)
        self._layers_panel = LayersPanel(self._scene)
        self._layers_dock = QDockWidget("Layers", self)
        self._layers_dock.setObjectName("LayersDock")
        self._layers_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea
        )
        self._layers_dock.setMinimumWidth(150)
        self._layers_dock.setWidget(self._layers_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._layers_dock)

        # Debug geometry dock (hidden until a manual-tabs run completes)
        self._debug_dock = DebugGeometryDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._debug_dock)

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Ready")

        self._cursor_label = QLabel()
        self._cursor_label.setStyleSheet("QLabel { margin-right: 6px; min-width: 140px; }")
        self._status.addPermanentWidget(self._cursor_label)

        self._panel_size_label = QLabel()
        self._panel_size_label.setStyleSheet("QLabel { margin-right: 6px; }")
        self._status.addPermanentWidget(self._panel_size_label)
        self._view.cursor_moved.connect(self._on_cursor_moved)
        self._view.cursor_left.connect(self._on_canvas_cursor_left)
        self._view.canvas_clicked.connect(self._on_canvas_clicked)
        self._view.zoom_changed.connect(self._on_zoom_changed)

        self._coordinator.run_started.connect(self._on_run_started)
        self._coordinator.run_finished.connect(self._on_run_finished)
        self._coordinator.run_failed.connect(self._on_run_failed)

        self._scene.fiducials_offset_changed.connect(self._on_fiducials_dragged)
        self._scene.fiducials_remove_requested.connect(self._on_fiducials_remove)
        self._scene.fiducials_reset_requested.connect(self._on_fiducials_reset)

        self._scene.tooling_offset_changed.connect(self._on_tooling_dragged)
        self._scene.tooling_remove_requested.connect(self._on_tooling_remove)
        self._scene.tooling_reset_requested.connect(self._on_tooling_reset)

        self._scene.text_offset_changed.connect(self._on_text_dragged)

        self._scene.layers_loaded.connect(self._layers_panel.populate)
        self._scene.layers_loaded.connect(self._text_panel.populate_layers)
        self._scene.panel_size_changed.connect(self._on_panel_size_changed)

        # Board overlay path
        self._layout_panel.boards_selected.connect(self._on_boards_selected)
        self._layout_panel.boards_moved.connect(self._on_boards_selected)
        self._scene.board_positions_updated.connect(self._layout_panel.apply_board_drop)

        # Legacy single-board path (grid mode → tabs panel)
        self._layout_panel.board_highlighted.connect(self._on_board_highlighted)

        self._view.refresh_requested.connect(self._refresh_now)

        self._tabs_panel.tab_selected.connect(self._scene.select_tab_marker)
        self._tabs_panel.tab_delete_requested.connect(self._on_tab_delete_from_panel)
        self._tabs_panel.tab_list_hovered.connect(self._scene.select_tab_marker)
        self._layout_panel.board_hovered.connect(self._on_board_table_hovered)
        self._layout_panel.board_hover_cleared.connect(self._on_board_hover_cleared)

        self._param_tabs.currentChanged.connect(self._on_param_tab_changed)
        self._view.add_tab_requested.connect(self._on_add_tab_at_scene)

        # Float mode (paste) signals
        self._view.float_committed.connect(self._on_float_committed)
        self._view.float_cancelled.connect(self._on_float_cancelled)
        self._view.rotate_requested.connect(self._on_rotate_requested)

        self._build_menu()
        self._build_toolbar()
        self._restore_window_state()

        if board_path is not None:
            self._model.board_path = board_path

    # ------------------------------------------------------------------
    # Public functions
    # ------------------------------------------------------------------
    def opMode(self) -> str:
        """Return the operating mode (established by the param tabs)."""
        label = self._param_tabs.tabText(self._param_tabs.currentIndex())
        return label.lower()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_param_dock(self) -> tuple[QDockWidget, int]:
        dock = QDockWidget("Parameters", self)
        dock.setObjectName("ParametersDock")
        dock.setAllowedAreas(
            Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea
        )
        dock.setMinimumWidth(280)

        # Tab widget (establishes operating mode)
        self._param_tabs = QTabWidget()
        tabs = self._param_tabs

        self._layout_panel = LayoutPanel(self._model)
        tabs.addTab(self._layout_panel, "Layout")
        tabs.addTab(FramingPanel(self._model), "Framing")
        self._tabs_panel = TabsPanel(self._model)
        tabs.addTab(self._tabs_panel, "Tabs")
        tabs.addTab(CutsPanel(self._model), "Cuts")
        self._text_panel = TextPanel(self._model)
        tabs.addTab(self._text_panel, "Text")
        tabs.addTab(PostPanel(self._model), "Post")

        self._TABS_TAB_INDEX = 2

        # Auto Refresh toggle
        self._auto_refresh_btn = _ToolButtonOverlay()
        self._auto_refresh_btn.setIcon(qta.icon("mdi6.refresh-auto"))
        self._auto_refresh_btn.setIconSize(QSize(32, 32))
        self._auto_refresh_btn.setFixedSize(QSize(36, 36))
        self._auto_refresh_btn.setCheckable(True)
        self._auto_refresh_btn.setChecked(True)
        self._auto_refresh_btn.setToolTip(
            "Auto Refresh — automatically re-run KiKit on parameter changes"
        )
        self._auto_refresh_btn.setStyleSheet("QToolButton { border: none; padding: 2px; }")
        self._auto_refresh_btn.toggled.connect(self._on_auto_refresh_toggled)

        # Auto fit (zoom to fit)
        self._auto_fit_btn = _ToolButtonOverlay()
        self._auto_fit_btn.setIcon(qta.icon("mdi6.fit-to-page-outline"))
        self._auto_fit_btn.setIconSize(QSize(32, 32))
        self._auto_fit_btn.setFixedSize(QSize(36, 36))
        self._auto_fit_btn.setCheckable(True)
        self._auto_fit_btn.setChecked(True)
        self._auto_fit_btn.setToolTip("Auto Fit — automatically zooms to fit on each refresh")
        self._auto_fit_btn.setStyleSheet("QToolButton { border: none; padding: 2px; }")
        self._auto_fit_btn.toggled.connect(self._on_auto_fit_toggled)

        toolbar_strip = QWidget()
        strip_layout = QHBoxLayout(toolbar_strip)
        strip_layout.setContentsMargins(2, 2, 2, 2)
        strip_layout.setSpacing(2)
        strip_layout.addWidget(self._auto_refresh_btn)
        strip_layout.addWidget(self._auto_fit_btn)
        strip_layout.addStretch()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(tabs)
        layout.addWidget(toolbar_strip)

        dock.setWidget(container)
        preferred_width = tabs.tabBar().sizeHint().width() + 16
        return dock, preferred_width

    def _build_menu(self) -> None:
        from PySide6.QtGui import QAction as _QAction
        from PySide6.QtGui import QKeySequence as _QKS

        bar = self.menuBar()

        # File menu
        file_menu = bar.addMenu("&File")
        _new_act = _QAction(qta.icon("mdi6.folder-plus-outline"), "&New Config", self)
        _new_act.setShortcut(_QKS("Ctrl+N"))
        _new_act.triggered.connect(self._new_config)
        file_menu.addAction(_new_act)

        file_menu.addSeparator()
        _open_act = _QAction(qta.icon("mdi6.folder-open-outline"), "&Open Config…", self)
        _open_act.setShortcut(_QKS("Ctrl+O"))
        _open_act.triggered.connect(self._open_config)
        file_menu.addAction(_open_act)
        self._recent_menu: QMenu = file_menu.addMenu("Open &Recent")
        self._rebuild_recent_menu()

        file_menu.addSeparator()
        self._save_action = _QAction(qta.icon("mdi6.content-save"), "&Save Config", self)
        self._save_action.setShortcut(_QKS("Ctrl+S"))
        self._save_action.triggered.connect(self._save_config)
        file_menu.addAction(self._save_action)
        file_menu.addAction("Save Config &As…", self._save_config_as, "Ctrl+Shift+S")

        file_menu.addSeparator()
        self._export_action = _QAction(qta.icon("mdi6.file-export"), "&Export Panel…", self)
        self._export_action.setShortcut(_QKS("Ctrl+E"))
        self._export_action.triggered.connect(self._export_panel)
        file_menu.addAction(self._export_action)

        file_menu.addSeparator()
        file_menu.addAction("&Quit", self.close, "Ctrl+Q")

        # Edit menu
        edit_menu = bar.addMenu("&Edit")
        self._undo_action = _QAction(qta.icon("mdi6.undo"), "&Undo", self)
        self._undo_action.setShortcut(_QKS.StandardKey.Undo)
        self._undo_action.setEnabled(False)
        self._undo_action.triggered.connect(self._model.undo)
        edit_menu.addAction(self._undo_action)

        self._redo_action = _QAction(qta.icon("mdi6.redo"), "&Redo", self)
        self._redo_action.setShortcut(_QKS.StandardKey.Redo)
        self._redo_action.setEnabled(False)
        self._redo_action.triggered.connect(self._model.redo)
        edit_menu.addAction(self._redo_action)

        edit_menu.addSeparator()
        self._copy_action = _QAction(qta.icon("mdi6.content-copy"), "&Copy Board(s)", self)
        self._copy_action.setShortcut(_QKS.StandardKey.Copy)
        self._copy_action.triggered.connect(self._on_copy_boards)
        edit_menu.addAction(self._copy_action)

        self._paste_action = _QAction(qta.icon("mdi6.content-paste"), "&Paste Board(s)", self)
        self._paste_action.setShortcut(_QKS.StandardKey.Paste)
        self._paste_action.triggered.connect(self._on_paste_boards)
        edit_menu.addAction(self._paste_action)

        # View menu
        view_menu = bar.addMenu("&View")
        self._refresh_action = _QAction(qta.icon("mdi6.refresh"), "&Refresh", self)
        self._refresh_action.setShortcut(_QKS.StandardKey.Refresh)
        self._refresh_action.triggered.connect(self._refresh_now)
        view_menu.addAction(self._refresh_action)

        view_menu.addSeparator()
        self._zoomin_action = _QAction(qta.icon("mdi6.magnify-plus-outline"), "Zoom &In", self)
        self._zoomin_action.setShortcut(_QKS.StandardKey.ZoomIn)
        self._zoomin_action.triggered.connect(self._view.zoom_in)
        view_menu.addAction(self._zoomin_action)

        self._zoomout_action = _QAction(qta.icon("mdi6.magnify-minus-outline"), "Zoom &Out", self)
        self._zoomout_action.setShortcut(_QKS.StandardKey.ZoomOut)
        self._zoomout_action.triggered.connect(self._view.zoom_out)
        view_menu.addAction(self._zoomout_action)

        self._fitview_action = _QAction(qta.icon("mdi6.magnify-expand"), "Zoom to &Fit", self)
        self._fitview_action.setShortcut(Qt.CTRL | Qt.Key_0)
        self._fitview_action.triggered.connect(self._view.fit_panel)
        view_menu.addAction(self._fitview_action)

        view_menu.addSeparator()
        view_menu.addAction(self._layers_dock.toggleViewAction())

        # Help menu
        help_menu = bar.addMenu("&Help")
        help_menu.addAction("&About KiKit Viewer…", self._show_about)
        help_menu.addSeparator()
        help_menu.addAction(self._debug_dock.toggleViewAction())

        QShortcut(QKeySequence(Qt.Key.Key_Home), self, self._view.fit_panel)

    def _build_toolbar(self) -> None:
        from PySide6.QtWidgets import QToolBar as _QToolBar

        tb = _QToolBar("Main", self)
        tb.setObjectName("MainToolBar")
        tb.setMovable(False)
        tb.setIconSize(QSize(32, 32))

        # File handling section
        tb.addAction(self._save_action)
        tb.addAction(self._export_action)

        # Undo/Redo section
        tb.addSeparator()
        tb.addAction(self._undo_action)
        tb.addAction(self._redo_action)

        # Zoom section
        tb.addSeparator()
        tb.addAction(self._refresh_action)
        tb.addAction(self._zoomin_action)
        tb.addAction(self._zoomout_action)
        tb.addAction(self._fitview_action)
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, tb)

    # ------------------------------------------------------------------
    # Recently opened files
    # ------------------------------------------------------------------

    def _recent_paths(self) -> list[Path]:
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        raw = s.value("recentFiles", [])
        return [Path(p) for p in (raw if isinstance(raw, list) else [])]

    def _add_recent(self, path: Path) -> None:
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        existing = [p for p in (s.value("recentFiles", []) or []) if p != str(path)]
        s.setValue("recentFiles", [str(path)] + existing[: _MAX_RECENT - 1])
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self) -> None:
        self._recent_menu.clear()
        paths = self._recent_paths()
        if not paths:
            act = self._recent_menu.addAction("(none)")
            act.setEnabled(False)
            return
        for p in paths:
            self._recent_menu.addAction(
                p.name,
                lambda _checked=False, path=p: (
                    self._open_config_path(path) if self._prompt_save_if_dirty() else None
                ),
            )
        self._recent_menu.addSeparator()
        self._recent_menu.addAction("Clear Recent Files", self._clear_recent)

    def _clear_recent(self) -> None:
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).remove("recentFiles")
        self._rebuild_recent_menu()

    # ------------------------------------------------------------------
    # Window state persistence
    # ------------------------------------------------------------------

    def _restore_window_state(self) -> None:
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        geometry = s.value("windowGeometry")
        state = s.value("windowState")
        if geometry and state:
            self.restoreGeometry(geometry)
            self.restoreState(state)
        else:
            # First launch — apply default dock width from tab bar measurement
            self.resizeDocks(
                [self._param_dock],
                [self._param_dock_default_width],
                Qt.Orientation.Horizontal,
            )

    def closeEvent(self, event) -> None:
        if not self._prompt_save_if_dirty():
            event.ignore()
            return
        s = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        s.setValue("windowGeometry", self.saveGeometry())
        s.setValue("windowState", self.saveState())
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Status bar helpers
    # ------------------------------------------------------------------

    def _on_panel_size_changed(self, w: float, h: float) -> None:
        if w > 0 and h > 0:
            self._panel_size_label.setText(f"Panel Size: {w:.1f} × {h:.1f} mm")
        else:
            self._panel_size_label.clear()

    def _on_config_changed(self) -> None:
        self._config_dirty = True

    def _on_undo_state_changed(self, can_undo: bool, can_redo: bool) -> None:
        self._undo_action.setEnabled(can_undo)
        self._redo_action.setEnabled(can_redo)

    def _prompt_save_if_dirty(self) -> bool:
        """Return True if safe to discard the current config (not dirty, saved, or discarded)."""
        if not self._config_dirty:
            return True
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            "The config has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if reply == QMessageBox.StandardButton.Save:
            self._save_config()
            return not self._config_dirty  # False if Save As dialog was cancelled
        return reply == QMessageBox.StandardButton.Discard

    def _on_cursor_moved(self, x: float, y: float) -> None:
        self._cursor_label.setText(f"X {x:.2f}  Y {y:.2f}")
        if self._float_mode:
            # We're carrying a float.  Move it.
            self._scene.update_float_positions(x, y)
        else:
            # We aren't carrying a float, so we must be hovering
            hit_item = next(
                (i for i in self._scene.items(QPointF(x, y)) if isinstance(i, BoardOverlayItem)),
                None,
            )
            self._update_hover(hit_item.board_id if hit_item else None)

    def _on_canvas_cursor_left(self) -> None:
        self._cursor_label.clear()
        self._on_board_hover_cleared()

    def _on_zoom_changed(self, scale: float) -> None:
        for olay in self._overlay_items:
            olay.set_view_scale(scale)

    def _on_overlay_position_changed(self, _board_id: int, _cx: float, _cy: float) -> None:
        moves = {}
        for olay in self._overlay_items:
            if olay.isSelected():
                p = olay.pos()
                moves[olay.board_id] = (p.x(), p.y(), -olay.rotation() or 0.0)
        if moves:
            self._layout_panel.apply_board_drop(moves)

    def _on_overlay_tapped(self, board_id: int, modifiers) -> None:
        """Clean click (no drag) on a board overlay in Layout mode — update table selection."""
        if modifiers and (modifiers & Qt.KeyboardModifier.ControlModifier):
            current = set(self._layout_panel.selected)
            if board_id in current:
                current.discard(board_id)
            else:
                current.add(board_id)
            self._layout_panel.set_selected(sorted(current))
        else:
            self._layout_panel.select(board_id)

    def _on_canvas_clicked(self, x: float, y: float, modifiers=None) -> None:
        """Left-click on non-interactive canvas area — select or Ctrl+click to toggle."""
        for row in range(self._layout_panel.board_count):
            data = self._layout_panel.board_scene_data(row)
            if data is None:
                continue
            cx, cy, w, h, rot, _ = data
            rad = math.radians(rot)
            dx, dy = x - cx, y - cy
            lx = dx * math.cos(rad) + dy * math.sin(rad)
            ly = -dx * math.sin(rad) + dy * math.cos(rad)
            if abs(lx) <= w / 2 and abs(ly) <= h / 2:
                if modifiers and (modifiers & Qt.KeyboardModifier.ControlModifier):
                    current = set(self._layout_panel.selected)
                    if row in current:
                        current.discard(row)
                    else:
                        current.add(row)
                    self._layout_panel.set_selected(sorted(current))
                else:
                    self._layout_panel.select(row)
                return
        # Click landed on empty canvas — deselect all in manual mode
        if self._model.get("layout", "type") == "manual" and self._layout_panel.selected:
            self._layout_panel.set_selected([])

    def _on_board_table_hovered(self, row: int) -> None:
        self._update_hover(row)

    def _on_board_hover_cleared(self) -> None:
        self._update_hover(None)

    def _on_tab_marker_hovered(self, idx: int) -> None:
        self._tabs_panel.highlight_tab_row(idx)

    def _update_hover(self, row: int | None) -> None:
        if row == self._hover_board_idx:
            return
        self._hover_board_idx = row
        self._layout_panel.set_canvas_hover(row)

    def _set_status(self, message: str, error: bool = False) -> None:
        self._status.showMessage(message)
        self._status.setStyleSheet("QStatusBar { color: #e05555; }" if error else "")

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

    def _show_about(self) -> None:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as pkg_version

        try:
            ver = pkg_version("kikit-viewer")
        except PackageNotFoundError:
            ver = "unknown"

        box = QMessageBox(self)
        box.setWindowTitle("About KiKit Viewer")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText(
            f"<b>KiKit Viewer</b> v{ver}<br><br>"
            f"A KiCad plugin to extend the functionality of KiKit by "
            f"Jan Mrázek.  We offer a special thanks to Jan for his hard "
            f"work and excellent contribution.<br><br>"
            f'GitHub repo: <a href="https://github.com/AvetosDesign/KiKitViewer">'
            f"github.com/AvetosDesign/KiKitViewer</a>"
            f"<br>"
            f'KiKit repo: <a href="https://github.com/yaqwsx/KiKit">'
            f"github.com/yaqwsx/KiKit</a>"
        )
        box.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        box.exec()

    def _new_config(self) -> None:
        if not self._prompt_save_if_dirty():
            return
        self._scene.clear_panel()
        self._model.reset_to_defaults()
        self._config_dirty = False  # reset_to_defaults emits config_changed; override it
        self._set_status("New config")

    def _open_config(self) -> None:
        if not self._prompt_save_if_dirty():
            return
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Panel Config",
            "",
            "KiKit panel (*.kicad_panel);;KiKit config (*.kikit.json);;All files (*)",
        )
        if path:
            self._open_config_path(Path(path))

    def _open_config_path(self, path: Path) -> None:
        try:
            raw = serialization.load(path)
            meta = translation.viewer_meta(raw)
            self._scene.clear_panel()
            self._model.load_dict(raw)  # load_dict ignores unknown sections

            # Restore layer visibility; takes effect on the next render
            for name, visible in meta.get("layers", {}).items():
                self._scene.set_layer_visible(name, bool(visible))
            self._add_recent(path)
            self._config_dirty = False  # just loaded — nothing to save yet
        except Exception as exc:
            QMessageBox.critical(self, "Load failed", str(exc))

    def _save_config(self) -> None:
        path = self._model.default_config_path()
        if path is None:
            self._save_config_as()
            return
        self._write_config(path)

    def _save_config_as(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        default = str(self._model.default_config_path() or "panel.kicad_panel")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Panel Config",
            default,
            "KiKit panel (*.kicad_panel);;KiKit config (*.kikit.json);;All files (*)",
        )
        if path:
            self._write_config(Path(path))

    def _write_config(self, path: Path) -> None:
        try:
            config = translation.with_viewer_meta(
                self._model.as_dict(),
                {"layers": dict(self._scene._layer_visibility)},
            )
            serialization.save(config, path)
            self._config_dirty = False
            self._set_status(f"Config saved to {path.name}")
        except Exception as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def _export_panel(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        if self._last_preview is None or not self._last_preview.exists():
            QMessageBox.warning(
                self,
                "Nothing to export",
                "No panel has been generated yet. "
                "Make sure a board is loaded and the panel preview is showing.",
            )
            return

        default = str(self._model.default_panel_path() or "panel.kicad_pcb")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Panel PCB", default, "KiCad PCB (*.kicad_pcb);;All files (*)"
        )
        if not path:
            return

        try:
            shutil.copy2(self._last_preview, path)
            self._set_status(f"Panel exported to {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    # ------------------------------------------------------------------
    # Copy / Paste
    # ------------------------------------------------------------------

    def _on_copy_boards(self) -> None:
        try:
            if self._model.get("layout", "type") != "manual":
                return
        except KeyError:
            return
        positions = self._model.get("layout", "positions") or []
        self._board_clipboard = [
            dict(positions[row]) for row in self._layout_panel.selected if row < len(positions)
        ]

    def _on_paste_boards(self) -> None:
        if not self._board_clipboard:
            return
        try:
            if self._model.get("layout", "type") != "manual":
                return
        except KeyError:
            return
        if self._float_mode:
            return  # already in float mode

        origin = self._layout_panel.panel_origin()
        if origin is None:
            return
        ox, oy = origin
        w_h = self._layout_panel.board_size
        if w_h is None:
            return
        w, h = w_h
        svg = self._layout_panel.edge_cuts_svg or ""

        self._scene.set_float_overlays(self._board_clipboard, ox, oy, w, h, svg)
        self._view.enter_float_mode()
        self._float_mode = True

    # ------------------------------------------------------------------
    # Float mode (paste placement)
    # ------------------------------------------------------------------

    def _on_float_committed(self, cursor_cx: float, cursor_cy: float) -> None:
        self._scene.update_float_positions(cursor_cx, cursor_cy)
        finals = self._scene.float_final_positions()  # [(scene_cx, scene_cy, rot), ...]
        origin = self._layout_panel.panel_origin()
        if origin is None:
            self._cleanup_float()
            return
        ox, oy = origin
        positions = list(self._model.get("layout", "positions") or [])
        new_rows_start = len(positions)
        for scene_cx, scene_cy, rot in finals:
            positions.append(
                {
                    "x": round(scene_cx + ox, 3),
                    "y": round(scene_cy + oy, 3),
                    "rotation": round(rot, 3),
                }
            )
        self._cleanup_float()
        self._model.set("layout", "positions", positions)
        new_rows = list(range(new_rows_start, len(positions)))
        self._layout_panel.set_selected(new_rows)

    def _on_float_cancelled(self) -> None:
        self._cleanup_float()

    def _cleanup_float(self) -> None:
        self._scene.clear_float_overlays()
        self._view.exit_float_mode()
        self._float_mode = False

    def _on_rotate_requested(self, degrees: int) -> None:
        if self._model.get("layout", "type") != "manual":
            return
        if self._float_mode:
            self._scene.rotate_float_overlays(degrees)
        else:
            self._scene.rotate_board_overlays(degrees)

    # ------------------------------------------------------------------
    # OverlayOwner interface (called by BoardOverlayItem.apply_context)
    # ------------------------------------------------------------------

    def get_tab_positions(self) -> list[dict]:
        try:
            return list(self._model.get("tabs", "positions") or [])
        except KeyError:
            return []

    def is_first(self, board_id: int) -> bool:
        # Start with "Are we the tab target?"
        if self._tab_target_id is not None:
            return board_id == self._tab_target_id
        # Next check to see if we are the first of a selection
        selected = self._layout_panel.selected
        return board_id == (selected[0] if selected else 0)

    def manual_tabs(self) -> bool:
        try:
            return (
                # self._param_tabs.currentIndex() == self._TABS_TAB_INDEX
                # and
                self._model.get("tabs", "type") == "manual"
            )
        except KeyError:
            return False

    def manual_layout(self) -> bool:
        try:
            return self._model.get("layout", "type") == "manual"
        except KeyError:
            return False

    # ------------------------------------------------------------------
    # Overlay context broadcast
    # ------------------------------------------------------------------

    def _destroy_overlays(self) -> None:
        for olay in self._overlay_items:
            olay.deleteLater()
        self._overlay_items.clear()

    def _on_config_changed_overlay(self) -> None:
        self._on_config_changed()
        self._sync_tab_mode()

    def _on_boards_selected(self, boards: list[BoardEntry]) -> None:
        """
        Updates the selection state of the BoardOverlayItems in response to
        changes in selection in the layout widget.
        """
        # Extract the IDs for the selected boards
        selected_ids = {brd[0] for brd in boards}

        # Update our tab target to be the first board
        self._tab_target_id = boards[0][0] if boards else None

        # Update the selection state of all board overlays
        for olay in self._overlay_items:
            olay.setSelected(olay.board_id in selected_ids)
            olay.refresh_context()

    def _on_board_highlighted(self, data: BoardSceneData) -> None:
        self._on_boards_selected([(0, data)])

    # Run manager handling
    def _on_run_started(self) -> None:
        self._set_status("Running KiKit…")

    def _on_run_finished(self, panel_path: Path, result: dict) -> None:
        self._last_preview = panel_path
        layers = result.get("svgs", {})
        board_w = result.get("board_w", 0.0)
        board_h = result.get("board_h", 0.0)
        edges = result.get("board_edge_cuts_svg", "")
        outline_pts = result.get("board_outline_pts", [])
        if board_w and board_h:
            self._layout_panel.set_board_geometry(board_w, board_h, edges)
        if outline_pts:
            from kikit_viewer.geometry.board_outline import outline_from_points

            self._board_outline = outline_from_points(outline_pts)

        # Rebuild the scene
        self._destroy_overlays()
        self._scene.load_pcb_panel(panel_path, self._model.as_dict(), layers=layers)

        # Create the board overlays
        for i in range(self._layout_panel.board_count):
            brd_data: BoardEntry = (i, self._layout_panel.board_scene_data(i))
            olay = BoardOverlayItem(brd_data, self, outline=self._board_outline)
            self._scene.addItem(olay)
            olay.set_view_scale(self._view.transform().m11())

            # Connect the overlay to our signals
            olay.position_changed.connect(self._on_overlay_position_changed)
            olay.overlay_tapped.connect(self._on_overlay_tapped)
            olay.tapped.connect(self._on_tab_tapped)
            olay.tab_moved.connect(self._on_tab_moved)
            olay.tab_deleted.connect(self._on_tab_deleted)
            olay.tab_hovered.connect(self._on_tab_marker_hovered)

            # Connect to the overlay's signals
            self.opmode_changed.connect(olay.set_opmode)
            self._layout_panel.layout_type_changed.connect(olay.refresh_context)
            self._model.config_changed.connect(olay.refresh_context)

            # Add the board overlay to our list
            self._overlay_items.append(olay)

        # Sync new overlays to the currently active param tab before restoring highlight
        self.opmode_changed.emit(self.opMode())

        if self._auto_fit_btn.isChecked():
            self._view.fit_panel()
        self._layout_panel.restore_highlight()

        self._update_debug_dock()
        self._set_status("Panel updated")

    def _on_run_failed(self, message: str) -> None:
        self._set_status("KiKit error — see details", error=True)
        QMessageBox.critical(self, "KiKit run failed", message)

    # Fiducial handling
    def _on_fiducials_dragged(self, hoffset: float, voffset: float) -> None:
        self._model.set("fiducials", "hoffset", hoffset)
        self._model.set("fiducials", "voffset", voffset)

    def _on_fiducials_remove(self) -> None:
        self._model.set("fiducials", "type", "none")

    def _on_fiducials_reset(self) -> None:
        from kikit_viewer.config.schema import FIDUCIALS_FIELDS

        defaults = {f.key: f.default for f in FIDUCIALS_FIELDS}
        self._model.set("fiducials", "hoffset", defaults["hoffset"])
        self._model.set("fiducials", "voffset", defaults["voffset"])

    # Tooling handling
    def _on_tooling_dragged(self, hoffset: float, voffset: float) -> None:
        self._model.set("tooling", "hoffset", hoffset)
        self._model.set("tooling", "voffset", voffset)

    def _on_tooling_remove(self) -> None:
        self._model.set("tooling", "type", "none")

    def _on_tooling_reset(self) -> None:
        from kikit_viewer.config.schema import TOOLING_FIELDS

        defaults = {f.key: f.default for f in TOOLING_FIELDS}
        self._model.set("tooling", "hoffset", defaults["hoffset"])
        self._model.set("tooling", "voffset", defaults["voffset"])

    # Text handling
    def _on_text_dragged(self, hoffset: float, voffset: float) -> None:
        self._model.set("text", "hoffset", hoffset)
        self._model.set("text", "voffset", voffset)

    def _update_debug_dock(self) -> None:
        try:
            from kikit_viewer.plugins import manual_tabs

            dbg = dict(manual_tabs._last_debug_geometries)
        except Exception:
            return
        if dbg:
            self._debug_dock.show_geometries(**dbg)

    # ------------------------------------------------------------------
    # (PCB) Tab handling slots
    # ------------------------------------------------------------------

    def _clear_board_outline(self) -> None:
        self._board_outline = None

    # Return the active tab-placement overlay item, or None
    def _tab_overlay_item(self) -> BoardOverlayItem | None:
        if self._tab_target_id is not None:
            return self._overlay_items[self._tab_target_id]
        elif len(self._overlay_items):
            return self._overlay_items[0]
        else:
            return None

    # Handle right-click 'Add Tab Here' — scene coords delivered by PanelView
    def _on_add_tab_at_scene(self, scene_x: float, scene_y: float) -> None:
        item = self._tab_overlay_item()
        if item is not None:
            item.handle_scene_tap(scene_x, scene_y)

    # Tab placed by clicking the board highlight — coords already snapped by the item
    def _on_tab_tapped(
        self, board_id: int, local_x: float, local_y: float, angle_deg: float
    ) -> None:
        if self._tab_target_id != board_id:
            self._tab_target_id = board_id
            self._layout_panel.set_selected([board_id])
            for olay in self._overlay_items:
                olay.refresh_context()
            return
        positions = list(self._model.get("tabs", "positions") or [])
        positions.append(
            {
                "x": round(local_x, 3),
                "y": round(local_y, 3),
                "a": round(angle_deg, 2),
            }
        )
        self._model.set("tabs", "positions", positions)
        item = self._overlay_items[board_id]
        if item is not None:
            item.set_tabs(positions)

    # Tab marker dragged — coords already snapped by the item
    def _on_tab_moved(
        self, board_id: int, idx: int, local_x: float, local_y: float, angle_deg: float
    ) -> None:
        positions = list(self._model.get("tabs", "positions") or [])
        if 0 <= idx < len(positions):
            positions[idx] = {
                "x": round(local_x, 3),
                "y": round(local_y, 3),
                "a": round(angle_deg, 2),
            }
            self._model.set("tabs", "positions", positions)
            item = self._overlay_items[board_id]
            if item is not None:
                item.set_tabs(positions)

    # Tab marker deleted via the canvas
    def _on_tab_deleted(self, board_id: int, idx: int) -> None:
        self._delete_tab_at(idx, board_id)

    # Tab deleted via the Tabs panel UI
    def _on_tab_delete_from_panel(self, idx: int) -> None:
        self._delete_tab_at(idx, board_id=None)

    def _delete_tab_at(self, idx: int, board_id: int | None) -> None:
        positions = list(self._model.get("tabs", "positions") or [])
        if 0 <= idx < len(positions):
            positions.pop(idx)
            self._model.set("tabs", "positions", positions)
            if board_id is None:
                item = self._tab_overlay_item()
            else:
                item = self._overlay_items[board_id]
            if item is not None:
                item.set_tabs(positions)

    def _sync_tab_mode(self) -> None:
        try:
            on_tabs = self.opMode() == "tabs"
            is_manual = self._model.get("tabs", "type") == "manual"
            self._view.set_manual_tab_mode(on_tabs and is_manual)
        except Exception:
            self._view.set_manual_tab_mode(False)

    # Refresh board overlays whenever the user switches parameter tabs so the
    # mode (Layout vs Tab) and tab markers stay in sync with the active tab.
    def _on_param_tab_changed(self, _index: int) -> None:
        self._layout_panel.restore_highlight()
        self._sync_tab_mode()
        self.opmode_changed.emit(self.opMode())

    # ------------------------------------------------------------------
    # Other slots
    # ------------------------------------------------------------------

    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        self._coordinator.auto_refresh = checked
        if checked:
            self._coordinator.run_now()

    def _on_auto_fit_toggled(self, checked: bool) -> None:
        if checked:
            self._view.fit_panel()

    def _refresh_now(self) -> None:
        self._coordinator.run_now()
