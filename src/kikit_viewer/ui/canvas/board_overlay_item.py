from __future__ import annotations

import enum
import math

from PySide6.QtCore import QByteArray, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

from kikit_viewer.ui.canvas.tab_marker_item import TabMarkerItem

_MM_PER_PX = 25.4 / 96.0


class BoardOverlayMode(enum.Enum):
    Layout = "layout"
    Tab    = "tab"


class BoardOverlayItem(QGraphicsObject):
    """
    Self-contained board highlight overlay — one instance per board.

    Layout mode: draggable; emits position_changed synchronously on drag release.
    Tab mode: not draggable; left-click converts scene coords to board-local, snaps
    to the pre-computed outline offset, and emits tapped.

    Created once per board and updated in-place via update_geometry() / set_tabs().
    Eliminates the destroy-and-recreate cycle that caused the tab highlight bug.
    """

    position_changed = Signal(int, float, float)              # board_id, new scene_cx, scene_cy
    overlay_tapped   = Signal(int, object)                    # board_id, Qt.KeyboardModifiers
    tapped           = Signal(int, float, float, float)       # board_id, local_x, local_y, angle_deg
    tab_moved        = Signal(int, int, float, float, float)  # board_id, idx, local_x, local_y, angle_deg
    tab_deleted      = Signal(int, int)                       # board_id, idx
    tab_hovered      = Signal(int)                            # tab marker idx

    def __init__(
        self,
        board_id: int,
        outline,            # LinearRing | None
        w_mm: float,
        h_mm: float,
        scene_cx: float,
        scene_cy: float,
        rotation_deg: float,
        svg: str = "",
        color: str = "#ffffff",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._board_id = board_id
        self._outline = outline
        self._w = w_mm
        self._h = h_mm
        self._rotation_deg = rotation_deg
        self._color = color
        self._mode = BoardOverlayMode.Layout
        self._fallback = False
        self._drag_start = None
        self._press_modifiers = Qt.KeyboardModifier.NoModifier
        self._tab_markers: list[TabMarkerItem] = []
        self._svg_renderer: QSvgRenderer | None = None

        # Pre-compute outline offset lines — constant for this board shape,
        # avoids repeated parallel_offset() calls on every tab snap.
        self._tabpointline = None
        self._outsetline = None
        self._precompute_offsets()

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(150)
        self.setPos(scene_cx, scene_cy)
        self.setRotation(-rotation_deg)

        self._build_svg_child(svg, color)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def board_id(self) -> int:
        return self._board_id

    @property
    def outline(self):
        return self._outline

    @property
    def mode(self) -> BoardOverlayMode:
        return self._mode

    @mode.setter
    def mode(self, value: BoardOverlayMode) -> None:
        if self._mode == value:
            return
        self._mode = value
        if value == BoardOverlayMode.Layout:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.clear_tabs()  # hide markers when leaving Tab mode
        else:
            self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
            self.setCursor(Qt.CursorShape.CrossCursor)

    # ------------------------------------------------------------------
    # In-place update API
    # ------------------------------------------------------------------

    def update_geometry(
        self,
        scene_cx: float,
        scene_cy: float,
        rotation_deg: float,
        outline=None,
        svg: str = "",
        color: str = "",
    ) -> None:
        """Update position/rotation, and optionally replace outline and SVG."""
        self._rotation_deg = rotation_deg
        self.setPos(scene_cx, scene_cy)
        self.setRotation(-rotation_deg)
        if outline is not None and outline is not self._outline:
            self._outline = outline
            self._precompute_offsets()
        if svg and color:
            for child in self.childItems():
                child.setParentItem(None)
            self._svg_renderer = None
            self._fallback = False
            self._color = color
            self._build_svg_child(svg, color)

    def set_tabs(self, positions: list[dict]) -> None:
        """Replace tab markers in-place. Does not emit any signals."""
        sc = self.scene()
        if sc:
            for marker in self._tab_markers:
                sc.removeItem(marker)
        self._tab_markers.clear()

        if not positions or self._mode != BoardOverlayMode.Tab:
            return

        cx = self.pos().x()
        cy = self.pos().y()
        rad = math.radians(-self._rotation_deg)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)

        for idx, pos in enumerate(positions):
            x_mm = float(pos.get("x", 0.0))
            y_mm = float(pos.get("y", 0.0))
            a_deg = float(pos.get("a", 0.0))
            sx = cx + x_mm * cos_r - y_mm * sin_r
            sy = cy + x_mm * sin_r + y_mm * cos_r
            marker = TabMarkerItem(idx, a_deg - self._rotation_deg)
            marker.setPos(sx, sy)
            marker.moved.connect(self._on_marker_moved)
            marker.delete_requested.connect(self._on_marker_delete_requested)
            marker.hovered.connect(self.tab_hovered)
            if sc:
                sc.addItem(marker)
            self._tab_markers.append(marker)

    # Process an externally-triggered scene-coordinate tap (right-click context menu)
    def handle_scene_tap(self, scene_x: float, scene_y: float) -> None:
        local_x, local_y = self._scene_to_local(scene_x, scene_y)
        lx, ly, angle = self._project(local_x, local_y)
        self.tapped.emit(self._board_id, lx, ly, angle)

    # Delete all tab markers
    def clear_tabs(self) -> None:
        sc = self.scene()
        if sc:
            for marker in self._tab_markers:
                sc.removeItem(marker)
        self._tab_markers.clear()

    # Select the tab marker at idx, deselecting all others
    def select_tab_marker(self, idx: int) -> None:
        for i, marker in enumerate(self._tab_markers):
            marker.setSelected(i == idx)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _precompute_offsets(self) -> None:
        if self._outline is None:
            self._tabpointline = None
            self._outsetline = None
            return
        try:
            side = "right" if self._outline.is_ccw else "left"

            # The tab point line is used to position the tabs.  It is 
            # just outside the board outline so points don't end up inside.
            self._tabpointline = self._outline.parallel_offset(0.1, side)

            # The outset line provides a target to help us determine the
            # normal from a given point on the tab point line.  Given a 
            # starting point on the tab point line, the nearest point on the
            # outset line is the end of the normal vector.
            self._outsetline   = self._outline.parallel_offset(1.0, side)

        except Exception:
            self._tabpointline = None
            self._outsetline = None

    def _build_svg_child(self, svg: str, color: str) -> None:
        from kikit_viewer.ui.canvas.scene import (
            _colorize_svg, _parse_svg_dim_mm, _set_stroke_width,
        )
        if svg:
            colored = _set_stroke_width(_colorize_svg(svg, color), 0.25)
            renderer = QSvgRenderer(QByteArray(colored.encode("utf-8")))
            if renderer.isValid():
                default_w = renderer.defaultSize().width()
                svg_w = _parse_svg_dim_mm(colored, "width") or self._w
                scale = (svg_w / default_w) if default_w > 0 else _MM_PER_PX
                child = QGraphicsSvgItem()
                child.setParentItem(self)
                child.setSharedRenderer(renderer)
                child.setScale(scale)
                child.setPos(-self._w / 2.0, -self._h / 2.0)
                self._svg_renderer = renderer
                return
        self._fallback = True
        self.update()

    def _scene_to_local(self, scene_x: float, scene_y: float) -> tuple[float, float]:
        """Convert scene coordinates to board-local mm (origin at board centre)."""
        cx, cy = self.pos().x(), self.pos().y()
        rad = math.radians(self._rotation_deg)
        dx, dy = scene_x - cx, scene_y - cy
        return (
            dx * math.cos(rad) - dy * math.sin(rad),
            dx * math.sin(rad) + dy * math.cos(rad),
        )

    def _project(self, local_x: float, local_y: float) -> tuple[float, float, float]:
        """
        Snap a given point to the nearest point on the snap-line and 
        compute outward normal angle.
        """
        if self._tabpointline is None or self._outsetline is None:
            return local_x, local_y, 0.0
        try:
            from shapely.geometry import Point
            from shapely.ops import nearest_points
            p = Point(local_x, local_y)
            p_snap, _ = nearest_points(self._tabpointline, p)
            p_end,  _ = nearest_points(self._outsetline,   p_snap)
            nx, ny = p_end.x - p_snap.x, p_end.y - p_snap.y
            return p_snap.x, p_snap.y, math.degrees(math.atan2(ny, nx))
        except Exception:
            return local_x, local_y, 0.0

    def _on_marker_moved(self, idx: int, scene_x: float, scene_y: float) -> None:
        local_x, local_y = self._scene_to_local(scene_x, scene_y)
        lx, ly, angle = self._project(local_x, local_y)
        self.tab_moved.emit(self._board_id, idx, lx, ly, angle)

    def _on_marker_delete_requested(self, idx: int) -> None:
        self.tab_deleted.emit(self._board_id, idx)

    # ------------------------------------------------------------------
    # QGraphicsObject overrides
    # ------------------------------------------------------------------

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
        event.accept()
        self._drag_start = self.pos()
        self._press_modifiers = event.modifiers()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        p = self.pos()
        bid = self._board_id
        mods = self._press_modifiers
        if self._drag_start is not None and p != self._drag_start:
            # Genuine drag — emit synchronously (no QTimer needed; item is not destroyed).
            self.position_changed.emit(bid, p.x(), p.y())
        elif self._drag_start is not None:
            if self._mode == BoardOverlayMode.Tab:
                sp = event.scenePos()
                local_x, local_y = self._scene_to_local(sp.x(), sp.y())
                lx, ly, angle = self._project(local_x, local_y)
                self.tapped.emit(bid, lx, ly, angle)
            else:
                self.overlay_tapped.emit(bid, mods)
        self._drag_start = None
