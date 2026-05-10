from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Signal
from PySide6.QtGui import QPen
from PySide6.QtWidgets import QGraphicsItem

from kikit_viewer.ui.canvas.handle_item import HandleItem, _HANDLE_PX


class TextHandleItem(HandleItem):
    """
    Draggable handle representing the text annotation anchor.

    Uses the same KiCad corner + offset coordinate convention as
    FiducialHandleItem: positive hoffset moves inward from the horizontal
    edge; positive voffset moves inward from the vertical edge.

    The handle is not constrained to the panel — text may legitimately sit
    outside the panel boundary.

    On mouse release, emits ``released`` with the updated (hoffset, voffset).
    """

    released = Signal(float, float)  # hoffset, voffset after drag

    def __init__(
        self,
        panel_rect: QRectF,
        corner: str,   # "tl" | "tr" | "bl" | "br"
        hoffset: float,
        voffset: float,
        parent=None,
    ) -> None:
        super().__init__("#E040FB", parent)  # magenta — distinct from cyan/amber handles
        self._panel_rect = panel_rect
        self._corner = corner
        self.setPos(self._offsets_to_scene(hoffset, voffset))

    # ------------------------------------------------------------------
    # Coordinate helpers (identical convention to FiducialHandleItem)
    # ------------------------------------------------------------------

    def _offsets_to_scene(self, hoffset: float, voffset: float) -> QPointF:
        r = self._panel_rect
        match self._corner:
            case "tl":
                return QPointF(r.left()  + hoffset, r.top()    + voffset)
            case "tr":
                return QPointF(r.right() - hoffset, r.top()    + voffset)
            case "bl":
                return QPointF(r.left()  + hoffset, r.bottom() - voffset)
            case _:  # "br"
                return QPointF(r.right() - hoffset, r.bottom() - voffset)

    def _scene_to_offsets(self, pos: QPointF) -> tuple[float, float]:
        r = self._panel_rect
        match self._corner:
            case "tl":
                return pos.x() - r.left(),  pos.y() - r.top()
            case "tr":
                return r.right() - pos.x(), pos.y() - r.top()
            case "bl":
                return pos.x() - r.left(),  r.bottom() - pos.y()
            case _:  # "br"
                return r.right() - pos.x(), r.bottom() - pos.y()

    # ------------------------------------------------------------------
    # QGraphicsItem overrides
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        s = _HANDLE_PX + 2
        return QRectF(-s, -s, s * 2, s * 2)

    def paint(self, painter, option, widget=None) -> None:
        size = _HANDLE_PX * (1.2 if self._hovered else 1.0)
        color = self._color.lighter(140) if self._hovered else self._color
        pen = QPen(color)
        pen.setWidthF(2.0 if self._hovered else 1.5)
        painter.setPen(pen)
        # Draw a "T" shape: horizontal bar at top, vertical stem below
        painter.drawLine(QPointF(-size, -size), QPointF(size, -size))
        painter.drawLine(QPointF(0.0,   -size), QPointF(0.0,  size))

    def itemChange(self, change, value):
        # No position constraint — text may sit outside the panel.
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        h, v = self._scene_to_offsets(self.pos())
        self.released.emit(round(h, 3), round(v, 3))
