from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject


# Screen-space half-size of any handle in pixels
_HANDLE_PX = 6


class HandleItem(QGraphicsObject):
    """
    Base class for interactive canvas overlay handles.

    Drawn at a fixed screen size regardless of zoom (ItemIgnoresTransformations).
    Subclasses implement drag constraints and signal emission.

    Scene position is the handle's anchor in mm scene coordinates.
    """

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._hovered = False

        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsScenePositionChanges)
        self.setAcceptHoverEvents(True)
        self.setAcceptedMouseButtons(Qt.MouseButton.AllButtons)
        self.setZValue(200)

    # ------------------------------------------------------------------
    # QGraphicsItem interface
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        s = _HANDLE_PX + 2
        return QRectF(-s, -s, s * 2, s * 2)

    def paint(self, painter, option, widget=None) -> None:
        size = _HANDLE_PX * (1.2 if self._hovered else 1.0)
        color = self._color.lighter(140) if self._hovered else self._color
        pen = QPen(color.darker(150))
        pen.setWidthF(1.5)
        painter.setPen(pen)
        painter.setBrush(QBrush(color))
        painter.drawRect(QRectF(-size, -size, size * 2, size * 2))

    def hoverEnterEvent(self, event) -> None:
        self._hovered = True
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.update()

    def hoverLeaveEvent(self, event) -> None:
        self._hovered = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def show_context_menu(self, screen_pos) -> None:
        """Called by PanelView on right-click. Subclasses override to show a menu."""

    # ------------------------------------------------------------------
    # Drag helpers for subclasses
    # ------------------------------------------------------------------

    def _constrain_to_bounds(
        self,
        pos: QPointF,
        min_x: float, max_x: float,
        min_y: float, max_y: float,
    ) -> QPointF:
        return QPointF(
            max(min_x, min(max_x, pos.x())),
            max(min_y, min(max_y, pos.y())),
        )
