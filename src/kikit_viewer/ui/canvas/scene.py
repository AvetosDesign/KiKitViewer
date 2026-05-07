from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsScene

from kikit_viewer.renderer.color_theme import load_layer_colors
from kikit_viewer.renderer.pcbnew_renderer import PcbnewSvgRenderer
from kikit_viewer.ui.canvas.fiducial_handle_item import (
    FiducialHandleItem,
)
from kikit_viewer.ui.canvas.fiducial_handle_item import (
    corners_for_type as fiducial_corners_for_type,
)
from kikit_viewer.ui.canvas.tooling_handle_item import (
    ToolingHandleItem,
)
from kikit_viewer.ui.canvas.tooling_handle_item import (
    corners_for_type as tooling_corners_for_type,
)
from kikit_viewer.ui.canvas.tab_marker_item import TabMarkerItem

# Qt 6 SVG renderer assumes 96 DPI; 1 px = this many mm
_MM_PER_PX = 25.4 / 96.0


class _BoardHighlightItem(QGraphicsObject):
    """
    Draggable board outline container for the table-layout highlight overlay.

    The actual outline is rendered by a child QGraphicsSvgItem (or, when no SVG
    is available, painted directly as a dashed rectangle).  This parent item
    carries ItemIsMovable so the user can drag the outline to a new position;
    releasing the mouse after a genuine drag emits released(row, scene_cx, scene_cy).
    The emission is deferred via QTimer so the scene can safely replace this item
    from within the connected slot without re-entering the mouse-event stack.
    """

    released = Signal(int, float, float)  # row, new scene_cx, scene_cy after drag
    tapped   = Signal(float, float)        # scene position of a click (no drag)

    def __init__(self, row: int, w_mm: float, h_mm: float,
                 opacity: float = 0.9, color: str = "#ffffff") -> None:
        super().__init__()
        self._row = row
        self._w = w_mm
        self._h = h_mm
        self._color = color
        self._fallback = False
        self._drag_start = None
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(150)
        self.setOpacity(opacity)

    def enable_fallback_rect(self) -> None:
        self._fallback = True
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(-self._w / 2.0, -self._h / 2.0, self._w, self._h)

    def paint(self, painter, option, widget=None) -> None:
        if not self._fallback:
            return
        pen = QPen(QColor(self._color))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self.boundingRect())

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        event.accept()  # ensure release is delivered even without ItemIsMovable
        self._drag_start = self.pos()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        p = self.pos()
        row = self._row
        if self._drag_start is not None and p != self._drag_start:
            QTimer.singleShot(0, lambda: self.released.emit(row, p.x(), p.y()))
        elif self._drag_start is not None:
            sp = event.scenePos()
            QTimer.singleShot(0, lambda: self.tapped.emit(sp.x(), sp.y()))
        self._drag_start = None

# Back-to-front draw order — unlisted layers are appended after.
# Inner copper layers sit between B_Cu and F_Cu; up to 30 are supported.
_LAYER_ORDER = (
    ["B_Cu", "B_Mask", "B_Paste", "B_Fab", "B_Silkscreen"]
    + [f"In{i}_Cu" for i in range(30, 0, -1)]
    + ["F_Cu", "F_Mask", "F_Paste", "F_Fab", "F_Silkscreen", "Edge_Cuts"]
)


class PanelScene(QGraphicsScene):
    """
    QGraphicsScene that displays the KiKit panel output as composited SVG layers
    with interactive overlay handles for editable parameters.

    Each layer's SVG is colorized and loaded into a QSvgRenderer in memory.
    After each render, fiducial handles are placed at the corners computed from
    the fiducials config section; dragging a handle emits fiducials_offset_changed.
    """

    fiducials_offset_changed = Signal(float, float)  # hoffset, voffset
    fiducials_remove_requested = Signal()
    fiducials_reset_requested = Signal()

    tooling_offset_changed = Signal(float, float)  # hoffset, voffset
    tooling_remove_requested = Signal()
    tooling_reset_requested = Signal()

    layers_loaded = Signal(list)   # list[str] of layer names after each render
    panel_size_changed = Signal(float, float)  # panel width_mm, height_mm (0,0 = none)
    board_position_updated = Signal(float, float)  # new scene_cx, scene_cy after drag (single-board path)
    boards_positions_updated = Signal(object)       # dict[int, tuple[float,float]] (multi-board path)

    tab_placement_requested    = Signal(float, float)  # scene x, y of click on highlight
    tab_marker_moved           = Signal(int, float, float)  # idx, new scene x, y
    tab_marker_delete_requested = Signal(int)               # idx
    tab_marker_hovered         = Signal(int)                # idx of hovered tab marker

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pcb_renderer = PcbnewSvgRenderer()
        self._layer_colors = load_layer_colors()
        # Strong references to QSvgRenderer objects — must outlive their items.
        self._svg_renderers: list[QSvgRenderer] = []
        self._panel_rect: QRectF | None = None
        self._layer_items: dict[str, QGraphicsSvgItem] = {}
        # Single-board highlight (tabs mode / grid mode)
        self._highlight_item: _BoardHighlightItem | None = None
        self._highlight_renderer: QSvgRenderer | None = None
        # Multi-board highlights (manual layout mode)
        self._highlight_items: dict[int, _BoardHighlightItem] = {}
        self._highlight_renderers: dict[int, QSvgRenderer] = {}
        self._drag_snapshots: dict[int, QPointF] = {}
        self._drag_emit_pending: bool = False
        # Hover overlay
        self._hover_item: _BoardHighlightItem | None = None
        self._hover_renderer: QSvgRenderer | None = None
        # Tab markers
        self._tab_markers: list[TabMarkerItem] = []
        self._partition_line_items: list = []
        # Float overlays (paste float mode)
        self._float_items: list[_BoardHighlightItem] = []
        self._float_renderers: list[QSvgRenderer] = []
        self._float_origins: list[tuple[float, float]] = []
        self._float_ref_cx: float = 0.0
        self._float_ref_cy: float = 0.0
        self._layer_visibility: dict[str, bool] = {
            "F_Fab": False,
            "B_Fab": False,
        }

    def load_panel(
        self,
        panel_path: Path,
        config: dict[str, dict[str, Any]] | None = None,
        svgs: dict[str, str] | None = None,
    ) -> None:
        """Render the panel PCB and replace all items in the scene."""
        self.clear()
        self._svg_renderers.clear()
        self._layer_items.clear()
        self._panel_rect = None
        self._highlight_item = None
        self._highlight_renderer = None
        self._highlight_items.clear()
        self._highlight_renderers.clear()
        self._drag_snapshots.clear()
        self._drag_emit_pending = False
        self._hover_item = None
        self._hover_renderer = None
        self._tab_markers.clear()
        self._partition_line_items.clear()
        self._float_items.clear()
        self._float_renderers.clear()
        self._float_origins.clear()

        if svgs is not None:
            layers = svgs
        else:
            try:
                layers = self._pcb_renderer.render_layers(panel_path)
            except Exception:
                return

        ordered = [n for n in _LAYER_ORDER if n in layers]
        ordered += [n for n in layers if n not in _LAYER_ORDER]

        panel_w: float | None = None
        panel_h: float | None = None

        for z, layer_name in enumerate(ordered):
            svg_content = layers[layer_name]
            color = self._layer_colors.get(layer_name, "#888888")
            svg_content = _colorize_svg(svg_content, color)

            renderer = QSvgRenderer(QByteArray(svg_content.encode("utf-8")))
            if not renderer.isValid():
                continue

            self._svg_renderers.append(renderer)  # keep alive

            item = QGraphicsSvgItem()
            item.setSharedRenderer(renderer)
            item.setZValue(float(z))
            item.setOpacity(0.85)

            svg_w_mm = _parse_svg_dim_mm(svg_content, "width")
            svg_h_mm = _parse_svg_dim_mm(svg_content, "height")
            default_w = renderer.defaultSize().width()
            scale = (svg_w_mm / default_w) if (svg_w_mm and default_w > 0) else _MM_PER_PX
            item.setScale(scale)

            item.setVisible(self._layer_visibility.get(layer_name, True))
            self._layer_items[layer_name] = item
            self.addItem(item)

            if panel_w is None and svg_w_mm:
                panel_w = svg_w_mm
            if panel_h is None and svg_h_mm:
                panel_h = svg_h_mm

        if panel_w and panel_h:
            self._panel_rect = QRectF(0.0, 0.0, panel_w, panel_h)
            if config is not None:
                self._add_fiducial_handles(config)
                self._add_tooling_handles(config)

        self.layers_loaded.emit(list(self._layer_items.keys()))
        if panel_w and panel_h:
            self.panel_size_changed.emit(panel_w, panel_h)
        else:
            self.panel_size_changed.emit(0.0, 0.0)

    def layer_color(self, name: str) -> str:
        return self._layer_colors.get(name, "#888888")

    def layer_visible(self, name: str) -> bool:
        return self._layer_visibility.get(name, True)

    def set_layer_visible(self, name: str, visible: bool) -> None:
        """Toggle a layer's visibility. State is remembered across re-renders."""
        self._layer_visibility[name] = visible
        item = self._layer_items.get(name)
        if item is not None:
            item.setVisible(visible)

    def clear_panel(self) -> None:
        self.clear()
        self._svg_renderers.clear()
        self._layer_items.clear()
        self._panel_rect = None
        self._highlight_item = None
        self._highlight_renderer = None
        self._highlight_items.clear()
        self._highlight_renderers.clear()
        self._drag_snapshots.clear()
        self._drag_emit_pending = False
        self._hover_item = None
        self._hover_renderer = None
        self._tab_markers.clear()
        self._partition_line_items.clear()
        self._float_items.clear()
        self._float_renderers.clear()
        self._float_origins.clear()
        self.panel_size_changed.emit(0.0, 0.0)

    def draw_partition_lines(self, centroids_mm: list[tuple[float, float]]) -> None:
        """Draw Voronoi partition lines in yellow for debugging tab placement."""
        for item in self._partition_line_items:
            self.removeItem(item)
        self._partition_line_items.clear()

        if len(centroids_mm) < 2 or self._panel_rect is None:
            return

        try:
            from shapely.geometry import MultiPoint
            from shapely.ops import voronoi_diagram
            from shapely.geometry import box
            from PySide6.QtGui import QPainterPath
            from PySide6.QtWidgets import QGraphicsPathItem

            r = self._panel_rect
            envelope = box(r.left() - 10, r.top() - 10,
                           r.right() + 10, r.bottom() + 10)
            mp = MultiPoint(centroids_mm)
            regions = voronoi_diagram(mp, envelope=envelope)

            pen = QPen(QColor("#ffff00"))
            pen.setCosmetic(True)
            pen.setWidthF(1.5)

            for region in regions.geoms:
                coords = list(region.exterior.coords)
                path = QPainterPath()
                path.moveTo(coords[0][0], coords[0][1])
                for x, y in coords[1:]:
                    path.lineTo(x, y)
                path.closeSubpath()
                item = QGraphicsPathItem(path)
                item.setPen(pen)
                item.setBrush(Qt.BrushStyle.NoBrush)
                item.setZValue(100)  # above panel layers, below highlight (150) and markers (200)
                self.addItem(item)
                self._partition_line_items.append(item)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Board highlight overlay — single board (tabs mode / grid mode)
    # ------------------------------------------------------------------

    def highlight_board(
        self,
        scene_cx: float,
        scene_cy: float,
        w_mm: float,
        h_mm: float,
        rotation_deg: float,
        edge_cuts_svg: str = "",
        tab_positions: list[dict] | None = None,
        opacity: float = 0.9,
        color: str = "#ffffff",
    ) -> None:
        """Overlay a draggable board outline centred at (scene_cx, scene_cy) in scene mm.

        Uses the Edge_Cuts SVG when available; falls back to a dashed rectangle.
        Dragging the overlay and releasing emits board_position_updated(cx, cy).

        If tab_positions is provided (list of {"x", "y", "a"} dicts in board-local mm),
        tab marker items are added and the highlight accepts click-to-place via tapped.
        """
        self.clear_board_hover()
        self.clear_board_highlight()

        container = _BoardHighlightItem(-1, w_mm, h_mm, opacity=opacity, color=color)
        container.setPos(scene_cx, scene_cy)
        container.setRotation(-rotation_deg)  # Qt CW+ needs negation to match KiCad CCW+
        container.released.connect(lambda row, cx, cy: self.board_position_updated.emit(cx, cy))
        self.addItem(container)
        self._highlight_item = container

        if edge_cuts_svg:
            colored = _set_stroke_width(_colorize_svg(edge_cuts_svg, color), 0.25)
            renderer = QSvgRenderer(QByteArray(colored.encode("utf-8")))
            if renderer.isValid():
                default_w = renderer.defaultSize().width()
                svg_w = _parse_svg_dim_mm(colored, "width") or w_mm
                scale = (svg_w / default_w) if default_w > 0 else _MM_PER_PX
                child = QGraphicsSvgItem()
                child.setParentItem(container)
                child.setSharedRenderer(renderer)
                child.setScale(scale)
                child.setPos(-w_mm / 2.0, -h_mm / 2.0)
                self._highlight_renderer = renderer  # keep alive
            else:
                container.enable_fallback_rect()
        else:
            container.enable_fallback_rect()

        # Tab markers (manual tabs mode) — lock the overlay in place
        if tab_positions is not None:
            container.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            container.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
            container.setCursor(Qt.CursorShape.CrossCursor)
            import math
            container.tapped.connect(self.tab_placement_requested)
            rad = math.radians(-rotation_deg)  # KiCad CCW+ needs negation in std-math formula
            cos_r = math.cos(rad)
            sin_r = math.sin(rad)
            for idx, pos in enumerate(tab_positions):
                x_mm = float(pos.get("x", 0.0))
                y_mm = float(pos.get("y", 0.0))
                a_deg = float(pos.get("a", 0.0))
                # Convert board-local to scene coords using KiCad CCW rotation
                sx = scene_cx + x_mm * cos_r - y_mm * sin_r
                sy = scene_cy + x_mm * sin_r + y_mm * cos_r
                marker = TabMarkerItem(idx, a_deg - rotation_deg)
                marker.setPos(sx, sy)
                marker.moved.connect(self.tab_marker_moved)
                marker.delete_requested.connect(self.tab_marker_delete_requested)
                marker.hovered.connect(self.tab_marker_hovered)
                self.addItem(marker)
                self._tab_markers.append(marker)

    # ------------------------------------------------------------------
    # Board highlight overlays — multiple boards (manual layout mode)
    # ------------------------------------------------------------------

    def set_board_overlays(
        self,
        overlays: list[tuple[int, float, float, float, float, float, str, bool]],
    ) -> None:
        """Show draggable outlines for all boards in manual layout mode.

        overlays: list of (row, scene_cx, scene_cy, w_mm, h_mm, rotation_deg, svg, is_selected)
        Selected boards are drawn at full opacity; unselected boards are dimmed.
        Dragging any selected board moves all selected boards together; releasing
        emits boards_positions_updated({row: (new_cx, new_cy), ...}).
        """
        self.clear_board_hover()
        self.clear_board_highlight()

        for row, cx, cy, w, h, rot, svg, is_selected in overlays:
            opacity = 0.9 if is_selected else 0.35
            z_val = 150 if is_selected else 145
            container = _BoardHighlightItem(row, w, h, opacity=opacity)
            container.setPos(cx, cy)
            container.setRotation(-rot)
            container.setZValue(z_val)
            if is_selected:
                container.setSelected(True)
            container.released.connect(self._on_highlight_released)
            self.addItem(container)
            self._highlight_items[row] = container

            if svg:
                color = "#ffffff" if is_selected else "#aaaaaa"
                colored = _set_stroke_width(_colorize_svg(svg, color), 0.25)
                renderer = QSvgRenderer(QByteArray(colored.encode("utf-8")))
                if renderer.isValid():
                    default_w = renderer.defaultSize().width()
                    svg_w = _parse_svg_dim_mm(colored, "width") or w
                    scale = (svg_w / default_w) if default_w > 0 else _MM_PER_PX
                    child = QGraphicsSvgItem()
                    child.setParentItem(container)
                    child.setSharedRenderer(renderer)
                    child.setScale(scale)
                    child.setPos(-w / 2.0, -h / 2.0)
                    self._highlight_renderers[row] = renderer
                else:
                    container.enable_fallback_rect()
            else:
                container.enable_fallback_rect()

    def mousePressEvent(self, event) -> None:
        # Snapshot positions of all multi-board overlays before any drag
        self._drag_snapshots = {row: QPointF(item.pos()) for row, item in self._highlight_items.items()}
        super().mousePressEvent(event)

    def _on_highlight_released(self, row: int, cx: float, cy: float) -> None:
        if self._drag_emit_pending:
            return
        moves: dict[int, tuple[float, float]] = {}
        for r, item in self._highlight_items.items():
            snap = self._drag_snapshots.get(r)
            cur = item.pos()
            if snap is not None and (
                abs(cur.x() - snap.x()) > 0.001 or abs(cur.y() - snap.y()) > 0.001
            ):
                moves[r] = (cur.x(), cur.y())
        if moves:
            self._drag_emit_pending = True
            self.boards_positions_updated.emit(moves)
            QTimer.singleShot(0, self._reset_drag_pending)

    def _reset_drag_pending(self) -> None:
        self._drag_emit_pending = False

    def select_tab_marker(self, idx: int) -> None:
        """Select the tab marker at idx, deselecting all others."""
        for i, marker in enumerate(self._tab_markers):
            marker.setSelected(i == idx)

    def hover_board(
        self,
        scene_cx: float,
        scene_cy: float,
        w_mm: float,
        h_mm: float,
        rotation_deg: float,
        edge_cuts_svg: str = "",
    ) -> None:
        """Show a dim non-interactive preview overlay. Does not affect the selected highlight."""
        self.clear_board_hover()
        container = _BoardHighlightItem(-1, w_mm, h_mm, opacity=0.4)
        container.setPos(scene_cx, scene_cy)
        container.setRotation(-rotation_deg)
        container.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        container.setZValue(140)
        container.setCursor(Qt.CursorShape.ArrowCursor)
        self.addItem(container)
        self._hover_item = container
        if edge_cuts_svg:
            colored = _set_stroke_width(_colorize_svg(edge_cuts_svg, "#ffffff"), 0.25)
            renderer = QSvgRenderer(QByteArray(colored.encode("utf-8")))
            if renderer.isValid():
                default_w = renderer.defaultSize().width()
                svg_w = _parse_svg_dim_mm(colored, "width") or w_mm
                scale = (svg_w / default_w) if default_w > 0 else _MM_PER_PX
                child = QGraphicsSvgItem()
                child.setParentItem(container)
                child.setSharedRenderer(renderer)
                child.setScale(scale)
                child.setPos(-w_mm / 2.0, -h_mm / 2.0)
                self._hover_renderer = renderer
            else:
                container.enable_fallback_rect()
        else:
            container.enable_fallback_rect()

    def clear_board_hover(self) -> None:
        if self._hover_item is not None:
            self.removeItem(self._hover_item)
            self._hover_item = None
        self._hover_renderer = None

    def clear_board_highlight(self) -> None:
        for marker in self._tab_markers:
            self.removeItem(marker)
        self._tab_markers.clear()
        if self._highlight_item is not None:
            self.removeItem(self._highlight_item)
            self._highlight_item = None
        self._highlight_renderer = None
        for item in self._highlight_items.values():
            self.removeItem(item)
        self._highlight_items.clear()
        self._highlight_renderers.clear()

    # ------------------------------------------------------------------
    # Float overlays (copy/paste float mode)
    # ------------------------------------------------------------------

    def set_float_overlays(
        self,
        entries: list[dict],
        ox: float,
        oy: float,
        w_mm: float,
        h_mm: float,
        edge_cuts_svg: str = "",
    ) -> None:
        """Place semi-transparent floating board outlines that follow the cursor.

        entries: list of {"x": panel_mm, "y": panel_mm, "rotation": deg} (clipboard data)
        ox, oy: panel origin offsets (scene_x = pos_x - ox)
        w_mm, h_mm: board dimensions
        edge_cuts_svg: board outline SVG for rendering
        """
        self.clear_float_overlays()
        if not entries:
            return

        # Convert to scene coords and compute group centre
        scene_xs = [float(e.get("x", 0.0)) - ox for e in entries]
        scene_ys = [float(e.get("y", 0.0)) - oy for e in entries]
        ref_cx = sum(scene_xs) / len(scene_xs)
        ref_cy = sum(scene_ys) / len(scene_ys)
        self._float_ref_cx = ref_cx
        self._float_ref_cy = ref_cy

        for i, entry in enumerate(entries):
            sx = scene_xs[i]
            sy = scene_ys[i]
            self._float_origins.append((sx - ref_cx, sy - ref_cy))
            rot = float(entry.get("rotation", 0.0))

            item = _BoardHighlightItem(-1, w_mm, h_mm, opacity=0.5, color="#88ff88")
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            item.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
            item.setZValue(160)
            item.setPos(sx, sy)
            item.setRotation(-rot)
            item.setCursor(Qt.CursorShape.CrossCursor)
            self.addItem(item)
            self._float_items.append(item)

            if edge_cuts_svg:
                colored = _set_stroke_width(_colorize_svg(edge_cuts_svg, "#88ff88"), 0.25)
                renderer = QSvgRenderer(QByteArray(colored.encode("utf-8")))
                if renderer.isValid():
                    default_w = renderer.defaultSize().width()
                    svg_w = _parse_svg_dim_mm(colored, "width") or w_mm
                    scale = (svg_w / default_w) if default_w > 0 else _MM_PER_PX
                    child = QGraphicsSvgItem()
                    child.setParentItem(item)
                    child.setSharedRenderer(renderer)
                    child.setScale(scale)
                    child.setPos(-w_mm / 2.0, -h_mm / 2.0)
                    self._float_renderers.append(renderer)
                else:
                    item.enable_fallback_rect()
            else:
                item.enable_fallback_rect()

    def update_float_positions(self, cursor_cx: float, cursor_cy: float) -> None:
        """Move all float overlay items so the group centre tracks the cursor."""
        for item, (off_x, off_y) in zip(self._float_items, self._float_origins):
            item.setPos(cursor_cx + off_x, cursor_cy + off_y)

    def float_final_positions(self) -> list[tuple[float, float, float]]:
        """Return (scene_cx, scene_cy, rotation_deg) for each committed float item."""
        result = []
        for item in self._float_items:
            p = item.pos()
            rot = -item.rotation()  # undo Qt CW negation
            result.append((p.x(), p.y(), rot))
        return result

    def clear_float_overlays(self) -> None:
        for item in self._float_items:
            self.removeItem(item)
        self._float_items.clear()
        self._float_renderers.clear()
        self._float_origins.clear()

    # ------------------------------------------------------------------
    # Handle placement
    # ------------------------------------------------------------------

    def _add_fiducial_handles(self, config: dict[str, dict[str, Any]]) -> None:
        fid_cfg = config.get("fiducials", {})
        fid_type = str(fid_cfg.get("type", "none"))
        if fid_type == "none":
            return

        hoffset = float(fid_cfg.get("hoffset", 5.0))
        voffset = float(fid_cfg.get("voffset", 2.5))

        for corner in fiducial_corners_for_type(fid_type):
            handle = FiducialHandleItem(self._panel_rect, corner, hoffset, voffset)
            handle.released.connect(self.fiducials_offset_changed)
            handle.remove_requested.connect(self.fiducials_remove_requested)
            handle.reset_requested.connect(self.fiducials_reset_requested)
            self.addItem(handle)

    def _add_tooling_handles(self, config: dict[str, dict[str, Any]]) -> None:
        tool_cfg = config.get("tooling", {})
        tool_type = str(tool_cfg.get("type", "none"))
        if tool_type == "none":
            return

        hoffset = float(tool_cfg.get("hoffset", 2.5))
        voffset = float(tool_cfg.get("voffset", 2.5))

        for corner in tooling_corners_for_type(tool_type):
            handle = ToolingHandleItem(self._panel_rect, corner, hoffset, voffset)
            handle.released.connect(self.tooling_offset_changed)
            handle.remove_requested.connect(self.tooling_remove_requested)
            handle.reset_requested.connect(self.tooling_reset_requested)
            self.addItem(handle)


def _set_stroke_width(svg: str, width_mm: float) -> str:
    """Override all stroke widths in an SVG to a fixed value (in SVG user units = mm)."""
    w = f"{width_mm:.4f}"
    svg = re.sub(r'stroke-width="[^"]*"', f'stroke-width="{w}"', svg)
    svg = re.sub(r"stroke-width\s*:\s*[\d.]+", f"stroke-width:{w}", svg)
    return svg


def _colorize_svg(svg: str, color: str) -> str:
    """
    Replace pcbnew's headless-mode black (#000000) with the target layer color.

    pcbnew renders all layers as #000000 when no KiCad color theme is active.
    """
    return svg.replace("#000000", color)


def _parse_svg_dim_mm(svg: str, dim: str) -> float | None:
    """Parse a width= or height= attribute from the SVG root and return mm value."""
    m = re.search(rf'<svg\b[^>]*\b{dim}="([\d.]+)(mm|in|pt|px)?"', svg, re.IGNORECASE)
    if not m:
        return None
    value = float(m.group(1))
    unit = (m.group(2) or "px").lower()
    match unit:
        case "mm":
            return value
        case "in":
            return value * 25.4
        case "pt":
            return value * 25.4 / 72.0
        case _:
            return value * _MM_PER_PX
