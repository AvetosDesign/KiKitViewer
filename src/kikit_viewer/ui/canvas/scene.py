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
from kikit_viewer.ui.canvas.board_overlay_item import BoardOverlayItem
from kikit_viewer.ui.canvas.fiducial_handle_item import (
    FiducialHandleItem,
)
from kikit_viewer.ui.canvas.fiducial_handle_item import (
    corners_for_type as fiducial_corners_for_type,
)
from kikit_viewer.ui.canvas.text_handle_item import TextHandleItem
from kikit_viewer.ui.canvas.tooling_handle_item import (
    ToolingHandleItem,
)
from kikit_viewer.ui.canvas.tooling_handle_item import (
    corners_for_type as tooling_corners_for_type,
)

# Qt 6 SVG renderer assumes 96 DPI; 1 px = this many mm
_MM_PER_PX = 25.4 / 96.0


class _FloatItem(QGraphicsObject):
    """Dim, non-interactive board outline preview for float positioning."""

    def __init__(
        self,
        w_mm: float,
        h_mm: float,
        color: str = "#ffffff",
        opacity: float = 0.4,
        zvalue: int = 140,
    ) -> None:
        super().__init__()
        self._w = w_mm
        self._h = h_mm
        self._color = color
        self._fallback = False
        self.setZValue(zvalue)
        self.setOpacity(opacity)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)

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


# Back-to-front draw order — unlisted layers are appended after.
# Inner copper layers sit between B_Cu and F_Cu; up to 30 are supported.
_LAYER_ORDER = (
    ["Edge_Cuts", "B_Fab", "B_Silkscreen", "B_Paste", "B_Mask", "B_Cu"]
    + [f"In{i}_Cu" for i in range(30, 0, -1)]
    + ["F_Cu", "F_Mask", "F_Paste", "F_Silkscreen", "F_Fab"]
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

    text_offset_changed = Signal(float, float)  # hoffset, voffset

    layers_loaded = Signal(list)  # list[str] of layer names after each render
    panel_size_changed = Signal(float, float)  # panel width_mm, height_mm (0,0 = none)
    board_positions_updated = Signal(object)  # dict[int, tuple[float,float,float]] (cx,cy,rot)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._pcb_renderer = PcbnewSvgRenderer()
        self._layer_colors = load_layer_colors()
        # Strong references to QSvgRenderer objects — must outlive their items.
        self._svg_renderers: list[QSvgRenderer] = []
        self._panel_rect: QRectF | None = None
        self._layer_items: dict[str, QGraphicsSvgItem] = {}
        # Board overlay items (persistent per-board handles)
        self._overlay_items: list[BoardOverlayItem] = []
        self._drag_snapshots: list[QPointF] = []
        self._drag_emit_pending: bool = False
        # self._partition_line_items: list = []
        # Float overlays (paste float mode)
        self._float_items: list[_FloatItem] = []
        self._float_renderers: list[QSvgRenderer] = []
        self._float_origins: list[tuple[float, float]] = []
        self._float_ref_cx: float = 0.0
        self._float_ref_cy: float = 0.0
        self._layer_visibility: dict[str, bool] = {
            "F_Fab": False,
            "B_Fab": False,
        }

    def load_pcb_panel(
        self,
        panel_path: Path,
        config: dict[str, dict[str, Any]] | None = None,
        layers: dict[str, str] | None = None,
    ) -> None:
        """Render the panel PCB and replace all items in the scene."""
        self.clear()
        self._svg_renderers.clear()
        self._layer_items.clear()
        self._panel_rect = None
        # self._overlay_items.clear()
        self._drag_snapshots.clear()
        self._drag_emit_pending = False
        # self._partition_line_items.clear()
        self._float_items.clear()
        self._float_renderers.clear()
        self._float_origins.clear()

        if layers is None:
            try:
                layers = self._pcb_renderer.render_layers(panel_path)
            except Exception:
                return

        ordered = [n for n in _LAYER_ORDER if n in layers]
        ordered += [n for n in layers if n not in _LAYER_ORDER]

        panel_w: float | None = None
        panel_h: float | None = None

        for z, layer_name in enumerate(ordered):
            layer_content = layers[layer_name]
            color = self._layer_colors.get(layer_name, "#888888")
            layer_content = _colorize_svg(layer_content, color)

            renderer = QSvgRenderer(QByteArray(layer_content.encode("utf-8")))
            if not renderer.isValid():
                continue

            self._svg_renderers.append(renderer)  # keep alive

            item = QGraphicsSvgItem()
            item.setSharedRenderer(renderer)
            item.setZValue(float(z))
            item.setOpacity(0.85)

            svg_w_mm = _parse_svg_dim_mm(layer_content, "width")
            svg_h_mm = _parse_svg_dim_mm(layer_content, "height")
            default_w = renderer.defaultSize().width()
            scale = (svg_w_mm / default_w) if (svg_w_mm and default_w > 0) else _MM_PER_PX
            item.setScale(scale)

            item.setVisible(self._layer_visibility.get(layer_name, True))
            self._layer_items[layer_name] = item
            self.addItem(item)

            # Emperical: Calculate panel width/height by deducting line width
            # from the SVG width and height so the status bar gives the
            # correct dimensions.
            if panel_w is None and svg_w_mm:
                panel_w = svg_w_mm - 0.1
            if panel_h is None and svg_h_mm:
                panel_h = svg_h_mm - 0.1

        if panel_w and panel_h:
            self._panel_rect = QRectF(0.0, 0.0, panel_w, panel_h)
            if config is not None:
                self._add_fiducial_handles(config)
                self._add_tooling_handles(config)
                self._add_text_handle(config)

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
        # self._overlay_items.clear()
        self._drag_snapshots.clear()
        self._drag_emit_pending = False
        # self._partition_line_items.clear()
        self._float_items.clear()
        self._float_renderers.clear()
        self._float_origins.clear()
        self.panel_size_changed.emit(0.0, 0.0)

    #  def draw_partition_lines(self, centroids_mm: list[tuple[float, float]]) -> None:
    #      """Draw Voronoi partition lines in yellow for debugging tab placement."""
    #      for item in self._partition_line_items:
    #          self.removeItem(item)
    #      self._partition_line_items.clear()

    #      if len(centroids_mm) < 2 or self._panel_rect is None:
    #          return

    #      try:
    #          from PySide6.QtGui import QPainterPath
    #          from PySide6.QtWidgets import QGraphicsPathItem
    #          from shapely.geometry import MultiPoint, box
    #          from shapely.ops import voronoi_diagram

    #          r = self._panel_rect
    #          envelope = box(r.left() - 10, r.top() - 10, r.right() + 10, r.bottom() + 10)
    #          mp = MultiPoint(centroids_mm)
    #          regions = voronoi_diagram(mp, envelope=envelope)

    #          pen = QPen(QColor("#ffff00"))
    #          pen.setCosmetic(True)
    #          pen.setWidthF(1.5)

    #          for region in regions.geoms:
    #              coords = list(region.exterior.coords)
    #              path = QPainterPath()
    #              path.moveTo(coords[0][0], coords[0][1])
    #              for x, y in coords[1:]:
    #                  path.lineTo(x, y)
    #              path.closeSubpath()
    #              item = QGraphicsPathItem(path)
    #              item.setPen(pen)
    #              item.setBrush(Qt.BrushStyle.NoBrush)
    #              item.setZValue(100)  # above panel layers, below highlight (150) and markers (200)
    #              self.addItem(item)
    #              self._partition_line_items.append(item)
    #      except Exception:
    #          pass

    # ------------------------------------------------------------------
    # Board overlay management
    # ------------------------------------------------------------------

    # def add_overlay(self, item: BoardOverlayItem) -> None:
    #     self._overlay_items[item.board_id] = item
    #     self.addItem(item)

    # def remove_overlay(self, board_id: int) -> None:
    #     item = self._overlay_items.pop(board_id, None)
    #     if item is not None:
    #         item.clear_tabs()
    #         self.removeItem(item)

    # def clear_overlays(self) -> None:
    #     for item in list(self._overlay_items):
    #         item.clear_tabs()
    #         self.removeItem(item)
    #     self._overlay_items.clear()

    # def overlay(self, board_id: int) -> BoardOverlayItem | None:
    #     return self._overlay_items[board_id]

    def mousePressEvent(self, event) -> None:
        # Snapshot overlay positions before any drag for multi-board move detection.
        self._drag_snapshots = [QPointF(item.pos()) for item in self._overlay_items]
        super().mousePressEvent(event)

    def _on_overlay_position_changed(self, board_id: int, cx: float, cy: float) -> None:
        if self._drag_emit_pending:
            return
        moves: dict[int, tuple[float, float, float]] = {}
        for item in self._overlay_items:
            snap = self._drag_snapshots.get(item.board_id)
            cur = item.pos()
            if snap is not None and (
                abs(cur.x() - snap.x()) > 0.001 or abs(cur.y() - snap.y()) > 0.001
            ):
                moves[item.board_id] = (cur.x(), cur.y(), -item.rotation() or 0.0)
        if moves:
            self._drag_emit_pending = True
            self.board_positions_updated.emit(moves)
            QTimer.singleShot(0, self._reset_drag_pending)

    def _reset_drag_pending(self) -> None:
        self._drag_emit_pending = False

    def select_tab_marker(self, idx: int) -> None:
        """Select the tab marker at idx across all overlay items."""
        for item in self._overlay_items:
            item.select_tab_marker(idx)

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

            item = _FloatItem(w_mm, h_mm, color="#88ff88", opacity=0.5, zvalue=160)
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
            rot = -item.rotation() or 0.0  # undo Qt CW negation; collapse -0.0
            result.append((p.x(), p.y(), rot))
        return result

    def clear_float_overlays(self) -> None:
        for item in self._float_items:
            self.removeItem(item)
        self._float_items.clear()
        self._float_renderers.clear()
        self._float_origins.clear()

    def _rotate_group(self, degrees: float, items: list) -> None:
        """Rotate a list of QGraphicsItems about their collective center."""
        group = self.createItemGroup(items)
        center = group.boundingRect().center()
        group.setTransformOriginPoint(center)
        group.setRotation(-degrees)
        self.destroyItemGroup(group)

    def rotate_float_overlays(self, degrees: float) -> None:
        """Rotate the float group around its collective center."""
        if not self._float_items:
            return

        cursor_x = self._float_items[0].pos().x() - self._float_origins[0][0]
        cursor_y = self._float_items[0].pos().y() - self._float_origins[0][1]

        # Rotate as a list of HoverItems (QGraphicsObjects)
        self._rotate_group(degrees, self._float_items)

        # Update our internal origin cache
        self._float_origins[:] = [
            (i.pos().x() - cursor_x, i.pos().y() - cursor_y) for i in self._float_items
        ]

    def rotate_board_overlays(self, degrees: float) -> None:
        """
        Rotate selected board overlays as a group.
        Emits board_positions_updated with new (cx, cy, rot) for each moved board.
        """
        selected = {item for item in self._overlay_items if item.isSelected()}
        if not selected:
            return

        # Rotate as a list of BoardOverlayItems (QGraphicsObjects)
        self._rotate_group(degrees, list(selected))

        # Notify the system about the moves
        moves: dict[int, tuple[float, float, float]] = {}
        for item in selected:
            p = item.pos()
            moves[item.board_id] = (p.x(), p.y(), degrees - item.rotation() or 0.0)
        self.board_positions_updated.emit(moves)

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

    def _add_text_handle(self, config: dict[str, dict[str, Any]]) -> None:
        text_cfg = config.get("text", {})
        if str(text_cfg.get("type", "none")) not in ("simple", "scripted"):
            return

        anchor = str(text_cfg.get("anchor", "tl"))
        hoffset = float(text_cfg.get("hoffset", 0.0))
        voffset = float(text_cfg.get("voffset", 0.0))

        handle = TextHandleItem(self._panel_rect, anchor, hoffset, voffset)
        handle.released.connect(self.text_offset_changed)
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
