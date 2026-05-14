from __future__ import annotations

import math
from typing import Protocol, TypeAlias

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainterPath, QPen, QPolygonF
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsPolygonItem

from kikit_viewer.ui.canvas.tab_marker_item import TabMarkerItem

_MM_PER_PX = 25.4 / 96.0
_HIGHLIGHT_WIDTH = 0.2


BoardSceneData: TypeAlias = tuple[float, float, float, float, float, str]
BoardEntry: TypeAlias = tuple[int, BoardSceneData | None]


class OverlayOwner(Protocol):
    """Accessor interface implemented by MainWindow; avoids circular imports."""

    def manual_tabs(self) -> bool: ...
    def manual_layout(self) -> bool: ...
    def get_tab_positions(self) -> list[dict]: ...
    def is_first(self, board_id: int) -> bool: ...


class _HighlightPolygon(QGraphicsPolygonItem):
    def __init__(self, parent, zval=140, opac=0.35, hover=False):
        super().__init__(parent)
        self.setParentItem(parent)
        self.setZValue(zval)
        self.setOpacity(opac)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(hover)
        self.setPen(QPen(QColor("transparent")))

    def hoverEnterEvent(self, event):
        """Update appearance on hover."""
        self.setPen(QPen(QColor("yellow"), 0.2))
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        """Reset appearance."""
        self.setPen(QPen(QColor("transparent")))
        super().hoverLeaveEvent(event)


class BoardOverlayItem(QGraphicsObject):
    """
    Self-contained board highlight overlay — one instance per board.

    apply_context() is called by MainWindow on every tab change to update
    visibility, cursor, and movable flag based on the active parameter tab.
    """

    position_changed = Signal(int, float, float)  # board_id, new scene_cx, scene_cy
    overlay_tapped = Signal(int, object)  # board_id, Qt.KeyboardModifiers
    tapped = Signal(int, float, float, float)  # board_id, local_x, local_y, angle_deg
    tab_moved = Signal(int, int, float, float, float)  # board_id, idx, local_x, local_y, angle_deg
    tab_deleted = Signal(int, int)  # board_id, idx
    tab_hovered = Signal(int)  # tab marker idx

    def __init__(self, board: BoardEntry, owner: OverlayOwner, outline=None, parent=None) -> None:
        super().__init__(parent)
        self._owner = owner

        id, scene_data = board
        self._board_id: int = id
        cx, cy, w, h, rot, svg = scene_data if scene_data is not None else (0, 0, 0, 0, 0, "")
        self._edgecut = outline
        self._w = w
        self._h = h
        self._rot_deg = rot
        self._color = "#ffffff"

        self._fallback = False
        self._drag_start = None
        self._press_modifiers = Qt.KeyboardModifier.NoModifier
        self._tab_markers: list[TabMarkerItem] = []
        self._view_scale: float = 1.0
        self._svg_renderer: QSvgRenderer | None = None
        self._opmode = "layout"

        # Create a board highlight for hovering
        self._hover_highlight = _HighlightPolygon(self, hover=True)

        # Create a board highlight for selecting
        self._select_highlight = _HighlightPolygon(self, zval=130, opac=0.9)
        self._select_highlight.setPen(QPen(QColor("transparent"), _HIGHLIGHT_WIDTH))

        # Pre-compute outline offset lines — constant for this board shape,
        # avoids repeated parallel_offset() calls on every tab snap.
        self._build_outlines()

        self.setPos(cx, cy)
        self.setRotation(-rot)
        self.setZValue(150)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setSelected(False)
        self.setVisible(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def board_id(self) -> int:
        return self._board_id

    @property
    def outline(self):
        return self._edgecut

    @property
    def is_in_tab_mode(self) -> bool:
        """True when apply_context placed this item in tab-placement mode."""
        return self._opmode == "tabs"

    # ------------------------------------------------------------------
    # In-place update API
    # ------------------------------------------------------------------

    # def update_geometry(
    #     self,
    #     scene_cx: float,
    #     scene_cy: float,
    #     rotation_deg: float,
    #     outline=None,
    #     svg: str = "",
    #     color: str = "",
    # ) -> None:
    #     """Update position/rotation, and optionally replace outline and SVG."""
    #     self._rot_deg = rotation_deg
    #     self.setPos(scene_cx, scene_cy)
    #     self.setRotation(-rotation_deg)
    #     if outline is not None and outline is not self._edgecut:
    #         self._edgecut = outline
    #         self._build_outlines()
    #     if svg and color:
    #         self._svg_renderer = None
    #         self._fallback = False
    #         self._color = color

    def set_view_scale(self, scale: float) -> None:
        """Update the counter-scale applied to tab markers so they stay fixed screen-size."""
        self._view_scale = scale
        for marker in self._tab_markers:
            marker.setScale(1.0 / scale)

    def set_tabs(self, positions: list[dict]) -> None:
        """Replace tab markers in-place. Does not emit any signals."""
        self.clear_tabs()

        # Make sure we have tabs to place, and that it's okay
        if not positions or not self.is_in_tab_mode:
            return

        for idx, pos in enumerate(positions):
            x_mm = float(pos.get("x", 0.0))
            y_mm = float(pos.get("y", 0.0))
            a_deg = float(pos.get("a", 0.0))
            marker = TabMarkerItem(idx, a_deg, parent=self)
            marker.setPos(x_mm, y_mm)
            marker.setScale(1.0 / self._view_scale)
            marker.moved.connect(self._on_marker_moved)
            marker.delete_requested.connect(self._on_marker_delete_requested)
            marker.hovered.connect(self.tab_hovered)
            self._tab_markers.append(marker)

    def handle_scene_tap(self, scene_x: float, scene_y: float) -> None:
        """Process an externally-triggered scene-coordinate tap (e.g., context menu)."""
        local_x, local_y = self._scene_to_local(scene_x, scene_y)
        lx, ly, angle = self._project(local_x, local_y)
        self.tapped.emit(self._board_id, lx, ly, angle)

    def clear_tabs(self) -> None:
        """Delete all tab markers."""
        for marker in self._tab_markers:
            marker.deleteLater()
        self._tab_markers.clear()

    def select_tab_marker(self, idx: int) -> None:
        """Select the tab marker at idx, deselecting all others"""
        for i, marker in enumerate(self._tab_markers):
            marker.setSelected(i == idx)

    def refresh_context(self) -> None:
        """Update visibility, cursor, and movable flag for the active parameter tab."""
        # Clean the slate
        self.clear_tabs()
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        highlight = False

        # Tab mode
        mainwin = self._owner
        if self._opmode == "tabs":
            # Show the tabs if we're in manual tab mode
            if mainwin.manual_tabs() and mainwin.is_first(self._board_id):
                self.set_tabs(mainwin.get_tab_positions())
                self.setCursor(Qt.CursorShape.CrossCursor)
                highlight = True

        # Layout mode
        if self._opmode == "layout":
            # Enable movement if we're in manual mode
            if mainwin.manual_layout():
                self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
                highlight = self.isSelected()

        # Selected highlight
        if highlight:
            self._select_highlight.setPen(QPen(QColor("white"), _HIGHLIGHT_WIDTH))
        else:
            self._select_highlight.setPen(QPen(QColor("transparent"), _HIGHLIGHT_WIDTH))

    def set_opmode(self, newmode: str) -> None:
        """A slot that is invoked when a parameter tab is selected."""
        if self._opmode == newmode:
            return
        # TODO: Validate newmode?
        self._opmode = newmode
        self.refresh_context()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_outlines(self) -> None:
        if self._edgecut is None:
            self._tabpointline = None
            self._outsetline = None
            self._outline = None
            return
        try:
            # Build a static board outline
            # Under some conditions, the board outline may not be visible due
            # to framing parameters, etc. This construct will make sure we
            # always have a visible reference of where the board edge is.
            points = [QPointF(x, y) for x, y in self._edgecut.coords]
            self._outline = QGraphicsPolygonItem(QPolygonF(points), parent=self)
            self._outline.setPen(QPen(QColor("white"), 0.12))
            self._outline.setOpacity(0.3)
            self._outline.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

            # The tab point line is used to position the tabs.  It is just
            # outside the board outline so candidate points won't be inside.
            side = "right" if self._edgecut.is_ccw else "left"
            self._tabpointline = self._edgecut.parallel_offset(0.1, side)

            # The outset line provides a target to help us determine the
            # normal from any starting point on the tab point line. Note that
            # this only works if the tab point line and the outset line are
            # equidistant.
            self._outsetline = self._edgecut.parallel_offset(1.0, side)

            # Update our highlight lines from the new tabpoint line
            # We could use the board outline directly, but using the tabpoint
            # line makes the highlight pop out just a little bit.
            points = [QPointF(x, y) for x, y in self._tabpointline.coords]
            polygon = QPolygonF(points)
            self._hover_highlight.setPolygon(polygon)
            self._select_highlight.setPolygon(polygon)

        except Exception:
            self._tabpointline = None
            self._outsetline = None
            self._outline = None

    def _scene_to_local(self, scene_x: float, scene_y: float) -> tuple[float, float]:
        """Convert scene coordinates to board-local mm (origin at board centre)."""
        pt = self.mapFromScene(scene_x, scene_y)
        return pt.x(), pt.y()

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
            p_end, _ = nearest_points(self._outsetline, p_snap)
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

    def shape(self) -> QPainterPath:
        if self._tabpointline is not None:
            try:
                coords = list(self._tabpointline.coords)
                if coords:
                    path = QPainterPath()
                    path.moveTo(coords[0][0], coords[0][1])
                    for x, y in coords[1:]:
                        path.lineTo(x, y)
                    path.closeSubpath()
                    return path
            except Exception:
                pass
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def paint(self, painter, option, widget=None) -> None:
        if not self._fallback:
            return
        pen = QPen(QColor(self._color))
        pen.setStyle(Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # TODO: Does this need to account for rotation?
        r = QRectF(-self._w / 2.0, -self._h / 2.0, self._w, self._h)
        painter.drawRect(r)

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
            if self.is_in_tab_mode:
                sp = event.scenePos()
                local_x, local_y = self._scene_to_local(sp.x(), sp.y())
                lx, ly, angle = self._project(local_x, local_y)
                self.tapped.emit(bid, lx, ly, angle)
            else:
                self.overlay_tapped.emit(bid, mods)
        self._drag_start = None
