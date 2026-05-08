from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QWheelEvent
from PySide6.QtWidgets import QGraphicsItem, QGraphicsView, QMenu


class PanelView(QGraphicsView):
    """
    QGraphicsView for the panel canvas.

    - Middle-mouse drag: pan
    - Ctrl+wheel: zoom
    - Ctrl+0: fit panel in view (wired up from MainWindow)
    - Right-click (no handle): canvas context menu with Refresh

    Scene units are millimetres (1 unit = 1 mm). The view transform handles
    conversion to screen pixels.
    """

    refresh_requested = Signal()
    cursor_moved  = Signal(float, float)  # scene x_mm, y_mm
    cursor_left   = Signal()
    canvas_clicked = Signal(float, float, object)  # scene x_mm, y_mm, Qt.KeyboardModifiers
    add_tab_requested = Signal(float, float)        # scene x_mm, y_mm — "Add Tab Here" context menu
    float_committed = Signal(float, float)          # scene x_mm, y_mm — left-click commits paste
    float_cancelled = Signal()                      # Escape or right-click cancels paste
    rotate_requested = Signal(int)                  # degrees: +90=CCW, -90=CW

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setBackgroundBrush(QBrush(QColor("#000000")))
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setMouseTracking(True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._panning = False
        self._pan_start = None
        self._manual_tab_mode = False
        self._float_mode = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_manual_tab_mode(self, enabled: bool) -> None:
        self._manual_tab_mode = enabled

    def enter_float_mode(self) -> None:
        """Enter paste-float mode: left-click commits, Escape/right-click cancels."""
        self._float_mode = True
        self.viewport().setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def exit_float_mode(self) -> None:
        """Exit paste-float mode and restore the default cursor."""
        self._float_mode = False
        self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    # Pan
    # ------------------------------------------------------------------

    def mousePressEvent(self, event):
        if self._float_mode:
            if event.button() == Qt.MouseButton.LeftButton:
                scene_pos = self.mapToScene(event.position().toPoint())
                self.float_committed.emit(scene_pos.x(), scene_pos.y())
                event.accept()
                return
            elif event.button() == Qt.MouseButton.RightButton:
                self.float_cancelled.emit()
                event.accept()
                return

        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            item = self.scene().itemAt(scene_pos, self.transform()) if self.scene() else None
            # Walk up the parent chain — the topmost hit may be a non-interactive child
            # (e.g. QGraphicsSvgItem inside _BoardHighlightItem) while an ancestor carries
            # the ItemIsMovable / ItemIsSelectable flag that enables dragging.
            check = item
            interactive = False
            while check is not None:
                if check.flags() & (
                    QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                    | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                ):
                    interactive = True
                    break
                check = check.parentItem()
            if not interactive:
                self.canvas_clicked.emit(scene_pos.x(), scene_pos.y(), event.modifiers())
                event.accept()
            else:
                super().mousePressEvent(event)
        elif event.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            # ItemIgnoresTransformations items need the view transform for correct
            # hit testing — pass self.transform() so the scene can map screen pixels
            # to scene coordinates.  contextMenuEvent lacks this and misses handles.
            scene_pos = self.mapToScene(event.position().toPoint())
            item = self.scene().itemAt(scene_pos, self.transform()) if self.scene() else None
            if item is not None and hasattr(item, "show_context_menu"):
                item.show_context_menu(event.globalPosition().toPoint())
                event.accept()
            else:
                menu = QMenu(self)
                add_tab_act = None
                if self._manual_tab_mode:
                    add_tab_act = menu.addAction("Add Tab Here")
                    menu.addSeparator()
                refresh_act = menu.addAction("Refresh")
                chosen = menu.exec(event.globalPosition().toPoint())
                if add_tab_act is not None and chosen is add_tab_act:
                    self.add_tab_requested.emit(scene_pos.x(), scene_pos.y())
                elif chosen is refresh_act:
                    self.refresh_requested.emit()
                event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        sp = self.mapToScene(event.position().toPoint())
        self.cursor_moved.emit(sp.x(), sp.y())
        if self._panning and self._pan_start is not None:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self.cursor_left.emit()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_start = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1.0 / 1.15
            self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if self._float_mode and event.key() == Qt.Key.Key_Escape:
            self.float_cancelled.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_R:
            degrees = -90 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 90
            self.rotate_requested.emit(degrees)
            event.accept()
            return
        if event.key() == Qt.Key.Key_Delete and self.scene():
            from kikit_viewer.ui.canvas.tab_marker_item import TabMarkerItem
            for item in self.scene().selectedItems():
                if isinstance(item, TabMarkerItem):
                    idx = item._idx
                    QTimer.singleShot(0, lambda i=idx, m=item: m.delete_requested.emit(i))
            event.accept()
        else:
            super().keyPressEvent(event)

    def zoom_in(self) -> None:
        self.scale(1.15, 1.15)

    def zoom_out(self) -> None:
        self.scale(1.0 / 1.15, 1.0 / 1.15)

    def fit_panel(self) -> None:
        """Fit all scene content in the viewport with a small margin."""
        if self.scene() is None:
            return
        rect = self.scene().itemsBoundingRect()
        if rect.isEmpty():
            return
        rect.adjust(-5, -5, 5, 5)
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
