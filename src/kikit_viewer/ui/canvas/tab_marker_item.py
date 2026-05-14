from __future__ import annotations

import math

from PySide6.QtCore import QLineF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

_RADIUS_PX = 5    # circle radius in screen pixels
_ARROW_PX  = 17   # arrow tip distance from centre in screen pixels


class TabMarkerItem(QGraphicsObject):
    """
    Draggable, selectable canvas overlay for a manual tab placement marker.

    Drawn as a filled yellow circle with an outward-direction arrow.
    Fixed screen-size (ItemIgnoresTransformations) — does not scale with zoom.

    Click to select; press Delete to remove (handled by PanelView.keyPressEvent).
    Drag to reposition; MainWindow re-snaps to the board outline on release.

    Signals
    -------
    moved(idx, scene_x, scene_y)
        Emitted (deferred) after the user drags and releases the marker.
    delete_requested(idx)
        Emitted when PanelView processes a Delete key press while this item
        is selected.
    """

    moved            = Signal(int, float, float)
    delete_requested = Signal(int)
    hovered          = Signal(int)

    def __init__(self, idx: int, angle_deg: float, parent=None) -> None:
        super().__init__(parent)
        self._idx = idx
        self._angle_deg = angle_deg
        self._drag_start = None

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(200)

    # ------------------------------------------------------------------
    # Geometry  (pixels — ItemIgnoresTransformations is active)
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        m = _ARROW_PX + 4
        return QRectF(-m, -m, m * 2, m * 2)

    def paint(self, painter, option, widget=None) -> None:
        selected = self.isSelected()

        # Selection ring drawn behind the circle
        if selected:
            sel_pen = QPen(QColor("#ffffff"))
            sel_pen.setWidthF(3.0)
            painter.setPen(sel_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(
                QRectF(-_RADIUS_PX - 3, -_RADIUS_PX - 3,
                       (_RADIUS_PX + 3) * 2, (_RADIUS_PX + 3) * 2)
            )

        # Circle
        pen = QPen(QColor("#c09020"))
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.setBrush(QBrush(QColor("#f0c040")))
        painter.drawEllipse(
            QRectF(-_RADIUS_PX, -_RADIUS_PX, _RADIUS_PX * 2, _RADIUS_PX * 2)
        )

        # Arrow from circle edge to tip
        rad = math.radians(self._angle_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        sx = _RADIUS_PX * cos_a
        sy = _RADIUS_PX * sin_a
        ex = _ARROW_PX * cos_a
        ey = _ARROW_PX * sin_a

        arrow_pen = QPen(QColor("#c09020"))
        arrow_pen.setWidthF(2.0)
        painter.setPen(arrow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(QLineF(sx, sy, ex, ey))

        # Arrowhead
        head_len = 6.0
        head_angle = math.radians(25)
        for side in (+1, -1):
            ax = ex - head_len * math.cos(rad - side * head_angle)
            ay = ey - head_len * math.sin(rad - side * head_angle)
            painter.drawLine(QLineF(ex, ey, ax, ay))

    # ------------------------------------------------------------------
    # Hover + drag interaction
    # ------------------------------------------------------------------

    def hoverEnterEvent(self, event) -> None:
        super().hoverEnterEvent(event)
        self.hovered.emit(self._idx)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self._drag_start = self.pos()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        p = self.pos()
        if self._drag_start is not None and p != self._drag_start:
            idx = self._idx
            QTimer.singleShot(0, lambda: self.moved.emit(idx, p.x(), p.y()))
        self._drag_start = None
