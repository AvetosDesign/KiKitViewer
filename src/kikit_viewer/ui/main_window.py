from __future__ import annotations

import math
import shutil
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QColor, QPainter
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
from kikit_viewer.ui.canvas.scene import PanelScene
from kikit_viewer.ui.canvas.view import PanelView
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


def _scene_to_board_local(
    sx: float, sy: float,
    cx: float, cy: float,
    rot_deg: float,
) -> tuple[float, float]:
    """Convert a scene-coordinate point to board-local mm (before rotation)."""
    rad = math.radians(rot_deg)  # KiCad CCW+ → inverse is CW on screen = std-math +rot_deg
    dx, dy = sx - cx, sy - cy
    return (
        dx * math.cos(rad) - dy * math.sin(rad),
        dx * math.sin(rad) + dy * math.cos(rad),
    )


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

    def __init__(self, board_path: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("KiKit Viewer")
        self.resize(1280, 800)

        self._model = ConfigModel(parent=self)
        self._coordinator = RunCoordinator(self._model, parent=self)
        self._last_preview: Path | None = None  # most recent successful panel output
        self._config_dirty: bool = False
        self._model.config_changed.connect(self._on_config_changed)
        self._model.undo_state_changed.connect(self._on_undo_state_changed)

        # Tab placement state — updated each time a board is highlighted
        self._last_highlight_cx: float = 0.0
        self._last_highlight_cy: float = 0.0
        self._last_highlight_rot: float = 0.0
        self._board_outline = None  # Shapely LinearRing, cached per board path
        self._model.board_path_changed.connect(lambda _: self._clear_board_outline())
        self._hover_board_idx: int | None = None

        # Canvas
        self._scene = PanelScene()
        self._view = PanelView()
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

        self._coordinator.run_started.connect(self._on_run_started)
        self._coordinator.run_finished.connect(self._on_run_finished)
        self._coordinator.run_failed.connect(self._on_run_failed)

        self._scene.fiducials_offset_changed.connect(self._on_fiducials_dragged)
        self._scene.fiducials_remove_requested.connect(self._on_fiducials_remove)
        self._scene.fiducials_reset_requested.connect(self._on_fiducials_reset)

        self._scene.tooling_offset_changed.connect(self._on_tooling_dragged)
        self._scene.tooling_remove_requested.connect(self._on_tooling_remove)
        self._scene.tooling_reset_requested.connect(self._on_tooling_reset)

        self._scene.layers_loaded.connect(self._layers_panel.populate)
        self._scene.panel_size_changed.connect(self._on_panel_size_changed)

        self._layout_panel.board_highlighted.connect(self._on_board_highlighted)
        self._layout_panel.board_deselected.connect(self._scene.clear_board_highlight)
        self._scene.board_position_updated.connect(self._layout_panel.apply_board_drop)

        self._view.refresh_requested.connect(self._refresh_now)

        self._scene.tab_placement_requested.connect(self._on_tab_placement_requested)
        self._scene.tab_marker_moved.connect(self._on_tab_marker_moved)
        self._scene.tab_marker_delete_requested.connect(self._on_tab_marker_delete_requested)

        self._tabs_panel.tab_selected.connect(self._scene.select_tab_marker)
        self._tabs_panel.tab_delete_requested.connect(self._on_tab_marker_delete_requested)
        self._tabs_panel.tab_list_hovered.connect(self._scene.select_tab_marker)
        self._scene.tab_marker_hovered.connect(self._on_tab_marker_hovered)
        self._layout_panel.board_hovered.connect(self._on_board_table_hovered)
        self._layout_panel.board_hover_cleared.connect(self._on_board_hover_cleared)

        self._param_tabs.currentChanged.connect(self._on_param_tab_changed)
        self._model.config_changed.connect(self._sync_tab_mode)
        self._view.add_tab_requested.connect(self._on_tab_placement_requested)

        self._build_menu()
        self._build_toolbar()
        self._restore_window_state()

        if board_path is not None:
            self._model.board_path = board_path

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

        self._param_tabs = QTabWidget()
        tabs = self._param_tabs
        self._layout_panel = LayoutPanel(self._model)
        self._tabs_panel   = TabsPanel(self._model)
        tabs.addTab(self._layout_panel, "Layout")
        tabs.addTab(FramingPanel(self._model), "Framing")
        tabs.addTab(self._tabs_panel,    "Tabs")
        tabs.addTab(CutsPanel(self._model),    "Cuts")
        tabs.addTab(TextPanel(self._model),    "Text")
        tabs.addTab(PostPanel(self._model),    "Post")
        self._TABS_TAB_INDEX = 2

        # Auto Refresh toggle
        self._auto_refresh_btn = _ToolButtonOverlay()
        self._auto_refresh_btn.setIcon(qta.icon('mdi6.refresh-auto'))
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
        self._auto_fit_btn.setIcon(qta.icon('mdi6.fit-to-page-outline'))
        self._auto_fit_btn.setIconSize(QSize(32, 32))
        self._auto_fit_btn.setFixedSize(QSize(36, 36))
        self._auto_fit_btn.setCheckable(True)
        self._auto_fit_btn.setChecked(True)
        self._auto_fit_btn.setToolTip(
            "Auto Fit — automatically zooms to fit on each refresh"
        )
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
        from PySide6.QtGui import QShortcut as _QShortcut
        
        bar = self.menuBar()

        # File menu
        file_menu = bar.addMenu("&File")
        _new_act = _QAction(qta.icon('mdi6.folder-plus-outline'), "&New Config", self)
        _new_act.setShortcut(_QKS("Ctrl+N"))
        _new_act.triggered.connect(self._new_config)
        file_menu.addAction(_new_act)

        file_menu.addSeparator()
        _open_act = _QAction(qta.icon('mdi6.folder-open-outline'), "&Open Config…", self)
        _open_act.setShortcut(_QKS("Ctrl+O"))
        _open_act.triggered.connect(self._open_config)
        file_menu.addAction(_open_act)
        self._recent_menu: QMenu = file_menu.addMenu("Open &Recent")
        self._rebuild_recent_menu()

        file_menu.addSeparator()
        self._save_action = _QAction(qta.icon('mdi6.content-save'), "&Save Config", self)
        self._save_action.setShortcut(_QKS("Ctrl+S"))
        self._save_action.triggered.connect(self._save_config)
        file_menu.addAction(self._save_action)
        file_menu.addAction("Save Config &As…", self._save_config_as, "Ctrl+Shift+S")

        file_menu.addSeparator()
        self._export_action = _QAction(qta.icon('mdi6.file-export'), "&Export Panel…", self)
        self._export_action.setShortcut(_QKS("Ctrl+E"))
        self._export_action.triggered.connect(self._export_panel)
        file_menu.addAction(self._export_action)

        file_menu.addSeparator()
        file_menu.addAction("&Quit", self.close, "Ctrl+Q")

        # Edit menu
        edit_menu = bar.addMenu("&Edit")
        self._undo_action = _QAction(qta.icon('mdi6.undo'), "&Undo", self)
        self._undo_action.setShortcut(_QKS.StandardKey.Undo)
        self._undo_action.setEnabled(False)
        self._undo_action.triggered.connect(self._model.undo)
        edit_menu.addAction(self._undo_action)

        self._redo_action = _QAction(qta.icon('mdi6.redo'), "&Redo", self)
        self._redo_action.setShortcut(_QKS.StandardKey.Redo)
        self._redo_action.setEnabled(False)
        self._redo_action.triggered.connect(self._model.redo)
        edit_menu.addAction(self._redo_action)

        # View menu
        view_menu = bar.addMenu("&View")
        self._refresh_action = _QAction(qta.icon('mdi6.refresh'), "&Refresh", self)
        self._refresh_action.setShortcut(_QKS.StandardKey.Refresh)
        self._refresh_action.triggered.connect(self._refresh_now)
        view_menu.addAction(self._refresh_action)

        view_menu.addSeparator()
        self._zoomin_action = _QAction(qta.icon('mdi6.magnify-plus-outline'), "Zoom &In", self)
        self._zoomin_action.setShortcut(_QKS.StandardKey.ZoomIn)
        self._zoomin_action.triggered.connect(self._view.zoom_in)
        view_menu.addAction(self._zoomin_action)

        self._zoomout_action = _QAction(qta.icon('mdi6.magnify-minus-outline'), "Zoom &Out", self)
        self._zoomout_action.setShortcut(_QKS.StandardKey.ZoomOut)
        self._zoomout_action.triggered.connect(self._view.zoom_out)
        view_menu.addAction(self._zoomout_action)
        
        self._fitview_action = _QAction(qta.icon('mdi6.magnify-expand'), "Zoom to &Fit", self)
        self._fitview_action.setShortcut(Qt.CTRL | Qt.Key_0)
        self._fitview_action.triggered.connect(self._view.fit_panel)
        view_menu.addAction(self._fitview_action)

        #view_menu.addSeparator()
        #_ar = _QAction3(qta.icon('mdi6.autorenew'), "&Auto Refresh", self)
        #_ar.setCheckable(True)
        #_ar.setChecked(self._auto_refresh_btn.isChecked())
        #_ar.toggled.connect(self._auto_refresh_btn.setChecked)
        #self._auto_refresh_btn.toggled.connect(_ar.setChecked)
        #view_menu.addAction(_ar)
        #_af = _QAction3(qta.icon('mdi6.fit-to-page-outline'), "Auto &Fit", self)
        #_af.setCheckable(True)
        #_af.setChecked(self._auto_fit_btn.isChecked())
        #_af.toggled.connect(self._auto_fit_btn.setChecked)
        #self._auto_fit_btn.toggled.connect(_af.setChecked)
        #view_menu.addAction(_af)
        
        view_menu.addSeparator()
        view_menu.addAction(self._layers_dock.toggleViewAction())
        view_menu.addAction(self._debug_dock.toggleViewAction())

        _QShortcut(_QKS(Qt.Key.Key_Home), self, self._view.fit_panel)

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
        s.setValue("recentFiles", [str(path)] + existing[:_MAX_RECENT - 1])
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
        self._update_hover_from_canvas(x, y)

    def _on_canvas_cursor_left(self) -> None:
        self._cursor_label.clear()
        self._on_board_hover_cleared()

    def _on_canvas_clicked(self, x: float, y: float) -> None:
        import math
        for row in range(self._layout_panel.board_count):
            data = self._layout_panel.board_scene_data(row)
            if data is None:
                continue
            cx, cy, w, h, rot, _ = data
            rad = math.radians(rot)
            dx, dy = x - cx, y - cy
            lx =  dx * math.cos(rad) + dy * math.sin(rad)
            ly = -dx * math.sin(rad) + dy * math.cos(rad)
            if abs(lx) <= w / 2 and abs(ly) <= h / 2:
                self._layout_panel.select_row(row)
                return

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
        self._layout_panel.set_canvas_hover_row(row)
        if row is None or row == self._layout_panel.active_row:
            self._scene.clear_board_hover()
            return
        data = self._layout_panel.board_scene_data(row)
        if data:
            cx, cy, w, h, rot, svg = data
            self._scene.hover_board(cx, cy, w, h, rot, svg)
        else:
            self._scene.clear_board_hover()

    def _update_hover_from_canvas(self, x: float, y: float) -> None:
        import math
        hit = -1
        for row in range(self._layout_panel.board_count):
            data = self._layout_panel.board_scene_data(row)
            if data is None:
                continue
            cx, cy, w, h, rot, _ = data
            rad = math.radians(rot)
            dx, dy = x - cx, y - cy
            lx =  dx * math.cos(rad) + dy * math.sin(rad)
            ly = -dx * math.sin(rad) + dy * math.cos(rad)
            if abs(lx) <= w / 2 and abs(ly) <= h / 2:
                hit = row
                break
        self._update_hover(hit if hit >= 0 else None)

    def _set_status(self, message: str, error: bool = False) -> None:
        self._status.showMessage(message)
        self._status.setStyleSheet(
            "QStatusBar { color: #e05555; }" if error else ""
        )

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

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
            self, "Open Panel Config", "",
            "KiKit panel (*.kicad_panel);;KiKit config (*.kikit.json);;All files (*)"
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
            self, "Save Panel Config", default,
            "KiKit panel (*.kicad_panel);;KiKit config (*.kikit.json);;All files (*)"
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
                self, "Nothing to export",
                "No panel has been generated yet. "
                "Make sure a board is loaded and the panel preview is showing."
            )
            return

        default = str(self._model.default_panel_path() or "panel.kicad_pcb")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Panel PCB", default,
            "KiCad PCB (*.kicad_pcb);;All files (*)"
        )
        if not path:
            return

        try:
            shutil.copy2(self._last_preview, path)
            self._set_status(f"Panel exported to {Path(path).name}")
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    # ------------------------------------------------------------------
    # Run coordinator slots
    # ------------------------------------------------------------------

    def _on_board_highlighted(self, svg: str, x: float, y: float, w: float, h: float, rot: float) -> None:
        self._last_highlight_cx = x
        self._last_highlight_cy = y
        self._last_highlight_rot = rot

        tab_positions: list[dict] | None = None
        try:
            on_tabs_tab = self._param_tabs.currentIndex() == self._TABS_TAB_INDEX
            if on_tabs_tab and self._model.get("tabs", "type") == "manual":
                tab_positions = list(self._model.get("tabs", "positions") or [])
        except KeyError:
            pass

        self._scene.highlight_board(x, y, w, h, rot, svg, tab_positions=tab_positions)

    def _on_run_started(self) -> None:
        self._set_status("Running KiKit…")

    def _on_run_finished(self, panel_path: Path, result: dict) -> None:
        self._last_preview = panel_path
        svgs = result.get("svgs", {})
        board_w = result.get("board_w", 0.0)
        board_h = result.get("board_h", 0.0)
        board_svg = result.get("board_edge_cuts_svg", "")
        outline_pts = result.get("board_outline_pts", [])
        if board_w and board_h:
            self._layout_panel.set_board_geometry(board_w, board_h, board_svg)
        if outline_pts:
            from kikit_viewer.geometry.board_outline import outline_from_points
            self._board_outline = outline_from_points(outline_pts)
        self._scene.load_panel(panel_path, self._model.as_dict(), svgs=svgs)
        if self._auto_fit_btn.isChecked():
            self._view.fit_panel()
        self._set_status("Panel updated")
        self._layout_panel.restore_highlight()
        self._maybe_highlight_first_board_for_tabs()
        self._update_debug_dock()

    def _update_debug_dock(self) -> None:
        try:
            from kikit_viewer.plugins import manual_tabs
            dbg = dict(manual_tabs._last_debug_geometries)
        except Exception:
            return
        if dbg:
            self._debug_dock.show_geometries(**dbg)

    def _on_run_failed(self, message: str) -> None:
        self._set_status("KiKit error — see details", error=True)
        QMessageBox.critical(self, "KiKit run failed", message)

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

    # ------------------------------------------------------------------
    # Tab placement slots
    # ------------------------------------------------------------------

    def _clear_board_outline(self) -> None:
        self._board_outline = None

    def _ensure_board_outline(self) -> None:
        # Outline is populated from the panel worker result in _on_run_finished.
        # Fallback: try load_outline directly (works when pcbnew is available,
        # e.g. running inside KiCad's Python for testing).
        if self._board_outline is not None:
            return
        board_path = self._model.board_path
        if board_path is None:
            return
        try:
            from kikit_viewer.geometry.board_outline import load_outline
            self._board_outline = load_outline(board_path)
        except Exception:
            pass

    def _on_tab_placement_requested(self, scene_x: float, scene_y: float) -> None:
        self._ensure_board_outline()
        local_x, local_y = _scene_to_board_local(
            scene_x, scene_y,
            self._last_highlight_cx, self._last_highlight_cy, self._last_highlight_rot,
        )
        if self._board_outline is not None:
            try:
                from kikit_viewer.geometry.board_outline import project_to_outline
                snapped_x, snapped_y, angle_deg = project_to_outline(
                    self._board_outline, local_x, local_y
                )
            except Exception:
                snapped_x, snapped_y, angle_deg = local_x, local_y, 0.0
        else:
            snapped_x, snapped_y, angle_deg = local_x, local_y, 0.0

        positions = list(self._model.get("tabs", "positions") or [])
        positions.append({
            "x": round(snapped_x, 3),
            "y": round(snapped_y, 3),
            "a": round(angle_deg, 2),
        })
        self._model.set("tabs", "positions", positions)

    def _on_tab_marker_moved(self, idx: int, scene_x: float, scene_y: float) -> None:
        self._ensure_board_outline()
        local_x, local_y = _scene_to_board_local(
            scene_x, scene_y,
            self._last_highlight_cx, self._last_highlight_cy, self._last_highlight_rot,
        )
        if self._board_outline is not None:
            try:
                from kikit_viewer.geometry.board_outline import project_to_outline
                snapped_x, snapped_y, angle_deg = project_to_outline(
                    self._board_outline, local_x, local_y
                )
            except Exception:
                snapped_x, snapped_y, angle_deg = local_x, local_y, 0.0
        else:
            snapped_x, snapped_y, angle_deg = local_x, local_y, 0.0

        positions = list(self._model.get("tabs", "positions") or [])
        if 0 <= idx < len(positions):
            positions[idx] = {
                "x": round(snapped_x, 3),
                "y": round(snapped_y, 3),
                "a": round(angle_deg, 2),
            }
            self._model.set("tabs", "positions", positions)

    def _on_tab_marker_delete_requested(self, idx: int) -> None:
        positions = list(self._model.get("tabs", "positions") or [])
        if 0 <= idx < len(positions):
            positions.pop(idx)
            self._model.set("tabs", "positions", positions)

    def _maybe_highlight_first_board_for_tabs(self) -> None:
        try:
            if self._model.get("tabs", "type") == "manual":
                self._layout_panel.highlight_first_board()
        except Exception:
            pass

    def _sync_tab_mode(self) -> None:
        try:
            on_tabs = self._param_tabs.currentIndex() == self._TABS_TAB_INDEX
            is_manual = self._model.get("tabs", "type") == "manual"
            self._view.set_manual_tab_mode(on_tabs and is_manual)
        except Exception:
            self._view.set_manual_tab_mode(False)

    def _on_param_tab_changed(self, _index: int) -> None:
        # Refresh the board highlight whenever the user switches parameter tabs so
        # that the mode (drag vs tap-to-place-tab) matches the active tab.
        self._layout_panel.restore_highlight()
        self._maybe_highlight_first_board_for_tabs()
        self._sync_tab_mode()

    def _on_auto_refresh_toggled(self, checked: bool) -> None:
        self._coordinator.auto_refresh = checked
        if checked:
            self._coordinator.run_now()

    def _on_auto_fit_toggled(self, checked: bool) -> None:
        if checked:
            self._view.fit_panel()

    def _refresh_now(self) -> None:
        self._coordinator.run_now()
