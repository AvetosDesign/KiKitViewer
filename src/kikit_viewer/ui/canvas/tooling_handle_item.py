from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Signal
from PySide6.QtGui import QBrush, QPen, Qt
from PySide6.QtWidgets import QGraphicsItem, QMenu

from kikit_viewer.ui.canvas.handle_item import HandleItem, _HANDLE_PX

_CORNERS_3HOLE = ("bl", "br", "tl")
_CORNERS_4HOLE = ("bl", "br", "tl", "tr")


def corners_for_type(tooling_type: str) -> tuple[str, ...]:
    if tooling_type == "4hole":
        return _CORNERS_4HOLE
    return _CORNERS_3HOLE


class ToolingHandleItem(HandleItem):
    """
    Draggable handle representing one tooling-hole corner position.

    Uses the same KiCad Y-axis convention as FiducialHandleItem: "tl"/"tr"
    sit at large scene Y (visual bottom) and "bl"/"br" at small scene Y
    (visual top).

    On mouse release, emits ``released`` with the new (hoffset, voffset).
    """

    released = Signal(float, float)
    remove_requested = Signal()
    reset_requested = Signal()

    def __init__(
        self,
        panel_rect: QRectF,
        corner: str,          # "tl" | "tr" | "bl" | "br"
        hoffset: float,
        voffset: float,
        parent=None,
    ) -> None:
        super().__init__("#FFB300", parent)  # amber
        self._panel_rect = panel_rect
        self._corner = corner
        self.setPos(self._offsets_to_scene(hoffset, voffset))

    # ------------------------------------------------------------------
    # Coordinate helpers  (identical convention to FiducialHandleItem)
    # ------------------------------------------------------------------

    def _offsets_to_scene(self, hoffset: float, voffset: float) -> QPointF:
        r = self._panel_rect
        match self._corner:
            case "tl": return QPointF(r.left()  + hoffset, r.bottom() - voffset)
            case "tr": return QPointF(r.right() - hoffset, r.bottom() - voffset)
            case "bl": return QPointF(r.left()  + hoffset, r.top()    + voffset)
            case _:    return QPointF(r.right() - hoffset, r.top()    + voffset)  # "br"

    def _scene_to_offsets(self, pos: QPointF) -> tuple[float, float]:
        r = self._panel_rect
        match self._corner:
            case "tl": return pos.x() - r.left(),  r.bottom() - pos.y()
            case "tr": return r.right() - pos.x(), r.bottom() - pos.y()
            case "bl": return pos.x() - r.left(),  pos.y() - r.top()
            case _:    return r.right() - pos.x(), pos.y() - r.top()  # "br"

    # ------------------------------------------------------------------
    # QGraphicsItem overrides
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        s = _HANDLE_PX * 2 + 2  # must cover the hovered paint size (_HANDLE_PX * 2.0)
        return QRectF(-s, -s, s * 2, s * 2)

    def paint(self, painter, option, widget=None) -> None:
        """Amber square outline — visually distinct from the cyan fiducial circles."""
        size = _HANDLE_PX * (2.0 if self._hovered else 1.25)
        color = self._color.lighter(140) if self._hovered else self._color
        pen = QPen(color)
        pen.setWidthF(2.0 if self._hovered else 1.0)
        painter.setPen(pen)
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawRect(QRectF(-size, -size, size * 2, size * 2))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            r = self._panel_rect
            hw, hh = r.width() / 2.0, r.height() / 2.0
            match self._corner:
                case "tl":
                    return self._constrain_to_bounds(
                        value, r.left(), r.left() + hw, r.bottom() - hh, r.bottom())
                case "tr":
                    return self._constrain_to_bounds(
                        value, r.right() - hw, r.right(), r.bottom() - hh, r.bottom())
                case "bl":
                    return self._constrain_to_bounds(
                        value, r.left(), r.left() + hw, r.top(), r.top() + hh)
                case _:  # "br"
                    return self._constrain_to_bounds(
                        value, r.right() - hw, r.right(), r.top(), r.top() + hh)
        return super().itemChange(change, value)

    def show_context_menu(self, screen_pos) -> None:
        menu = QMenu()
        reset_act = menu.addAction("Reset Offsets to Default")
        menu.addSeparator()
        remove_act = menu.addAction("Remove Tooling Holes")
        chosen = menu.exec(screen_pos)
        if chosen is reset_act:
            self.reset_requested.emit()
        elif chosen is remove_act:
            self.remove_requested.emit()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        h, v = self._scene_to_offsets(self.pos())
        self.released.emit(max(0.0, h), max(0.0, v))
