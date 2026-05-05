from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Signal
from PySide6.QtGui import QBrush, QPen, Qt
from PySide6.QtWidgets import QGraphicsItem, QMenu

from kikit_viewer.ui.canvas.handle_item import _HANDLE_PX, HandleItem

# Corners placed by each fiducial type
_CORNERS_3FID = ("bl", "br", "tl")
_CORNERS_4FID = ("bl", "br", "tl", "tr")


def corners_for_type(fid_type: str) -> tuple[str, ...]:
    if fid_type == "4fid":
        return _CORNERS_4FID
    return _CORNERS_3FID


class FiducialHandleItem(HandleItem):
    """
    Draggable handle representing one fiducial marker corner.

    KiCad's internal Y axis points upward, so pcbnew SVG output maps
    "top" (large internal Y) to large scene Y (visual bottom of screen).
    Corner labels follow KiCad's convention: "tl" is placed at visual
    bottom-left in scene space and "bl" at visual top-left.

    On mouse release, emits ``released`` with the new (hoffset, voffset)
    values so the caller can update the config model.
    """

    released = Signal(float, float)  # hoffset, voffset after drag
    remove_requested = Signal()
    reset_requested = Signal()

    def __init__(
        self,
        panel_rect: QRectF,
        corner: str,  # "tl" | "tr" | "bl" | "br"
        hoffset: float,
        voffset: float,
        parent=None,
    ) -> None:
        super().__init__("#00E5FF", parent)  # bright cyan
        self._panel_rect = panel_rect
        self._corner = corner
        self.setPos(self._offsets_to_scene(hoffset, voffset))

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    def _offsets_to_scene(self, hoffset: float, voffset: float) -> QPointF:
        """
        Map (hoffset, voffset) to scene position.

        KiCad's Y axis is inverted relative to scene Y, so "tl"/"tr" corners
        sit at large scene Y (visual bottom) and "bl"/"br" at small scene Y
        (visual top).
        """
        r = self._panel_rect
        match self._corner:
            case "tl":
                return QPointF(r.left() + hoffset, r.bottom() - voffset)
            case "tr":
                return QPointF(r.right() - hoffset, r.bottom() - voffset)
            case "bl":
                return QPointF(r.left() + hoffset, r.top() + voffset)
            case _:
                return QPointF(r.right() - hoffset, r.top() + voffset)  # "br"

    def _scene_to_offsets(self, pos: QPointF) -> tuple[float, float]:
        r = self._panel_rect
        match self._corner:
            case "tl":
                return pos.x() - r.left(), r.bottom() - pos.y()
            case "tr":
                return r.right() - pos.x(), r.bottom() - pos.y()
            case "bl":
                return pos.x() - r.left(), pos.y() - r.top()
            case _:
                return r.right() - pos.x(), pos.y() - r.top()  # "br"

    # ------------------------------------------------------------------
    # QGraphicsItem overrides
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        s = _HANDLE_PX * 2 + 2  # must cover the hovered paint size (_HANDLE_PX * 2.0)
        return QRectF(-s, -s, s * 2, s * 2)

    def paint(self, painter, option, widget=None) -> None:
        size = _HANDLE_PX * (2.0 if self._hovered else 1.25)
        color = self._color.lighter(140) if self._hovered else self._color
        pen = QPen(color)
        pen.setWidthF(2.0 if self._hovered else 1.0)
        painter.setPen(pen)
        painter.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        painter.drawEllipse(QRectF(-size, -size, size * 2, size * 2))

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            r = self._panel_rect
            hw, hh = r.width() / 2.0, r.height() / 2.0
            # tl/tr are at large scene Y (visual bottom); bl/br at small scene Y (visual top)
            match self._corner:
                case "tl":
                    return self._constrain_to_bounds(
                        value, r.left(), r.left() + hw, r.bottom() - hh, r.bottom()
                    )
                case "tr":
                    return self._constrain_to_bounds(
                        value, r.right() - hw, r.right(), r.bottom() - hh, r.bottom()
                    )
                case "bl":
                    return self._constrain_to_bounds(
                        value, r.left(), r.left() + hw, r.top(), r.top() + hh
                    )
                case _:  # "br"
                    return self._constrain_to_bounds(
                        value, r.right() - hw, r.right(), r.top(), r.top() + hh
                    )
        return super().itemChange(change, value)

    def show_context_menu(self, screen_pos) -> None:
        menu = QMenu()
        reset_act = menu.addAction("Reset Offsets to Default")
        menu.addSeparator()
        remove_act = menu.addAction("Remove Fiducials")
        chosen = menu.exec(screen_pos)
        if chosen is reset_act:
            self.reset_requested.emit()
        elif chosen is remove_act:
            self.remove_requested.emit()

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        h, v = self._scene_to_offsets(self.pos())
        self.released.emit(max(0.0, h), max(0.0, v))
