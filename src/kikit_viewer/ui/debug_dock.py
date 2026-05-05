from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDockWidget,
    QGraphicsPathItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QVBoxLayout,
    QWidget,
)


def _shapely_to_path(geom) -> QPainterPath:
    path = QPainterPath()
    _add_to_path(path, geom)
    return path


def _add_to_path(path: QPainterPath, geom) -> None:
    from shapely.geometry import (
        GeometryCollection,
        LinearRing,
        LineString,
        MultiLineString,
        MultiPolygon,
        Polygon,
    )
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        _ring_to_path(path, list(geom.exterior.coords))
        for interior in geom.interiors:
            _ring_to_path(path, list(interior.coords))
    elif isinstance(geom, MultiPolygon):
        for poly in geom.geoms:
            _add_to_path(path, poly)
    elif isinstance(geom, (LineString, LinearRing)):
        coords = list(geom.coords)
        if coords:
            path.moveTo(coords[0][0], coords[0][1])
            for x, y in coords[1:]:
                path.lineTo(x, y)
            if isinstance(geom, LinearRing):
                path.closeSubpath()
    elif isinstance(geom, MultiLineString):
        for line in geom.geoms:
            _add_to_path(path, line)
    elif isinstance(geom, GeometryCollection):
        for g in geom.geoms:
            _add_to_path(path, g)


def _ring_to_path(path: QPainterPath, coords: list) -> None:
    if not coords:
        return
    path.moveTo(coords[0][0], coords[0][1])
    for x, y in coords[1:]:
        path.lineTo(x, y)
    path.closeSubpath()


class DebugGeometryDock(QDockWidget):
    """
    Debug dock that visualises Shapely geometries from the partition line computation.

    Call show_geometries() after a manual-tabs run to display:
      - Panel outline  (gray dashed)
      - Substrates     (blue, transparent fill)
      - Free space     (green, transparent fill)
      - Partition lines (yellow)

    All geometries are expected in mm coordinates.
    """

    def __init__(self, parent=None) -> None:
        super().__init__("Debug: Partition Geometry", parent)
        self.setObjectName("DebugGeometryDock")
        self.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.BottomDockWidgetArea
        )
        self.hide()

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        legend = QLabel(
            '<span style="color:#808080">━</span> Panel &nbsp;&nbsp;'
            '<span style="color:#5599ff">■</span> Substrates &nbsp;&nbsp;'
            '<span style="color:#22cc55">■</span> Free space &nbsp;&nbsp;'
            '<span style="color:#ffdd00">━</span> Partition lines &nbsp;&nbsp;'
            '<span style="color:#ff8800">━</span> Backbone lines'
        )
        legend.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(legend)

        self._scene = QGraphicsScene()
        self._view = QGraphicsView(self._scene)
        self._view.setBackgroundBrush(QBrush(QColor("#1e1e1e")))
        self._view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._view.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        layout.addWidget(self._view, 1)

        self._info_label = QLabel()
        self._info_label.setWordWrap(True)
        self._info_label.setStyleSheet("QLabel { color: #aaaaaa; font-family: monospace; font-size: 10px; }")
        layout.addWidget(self._info_label)

        self.setWidget(container)

    # ------------------------------------------------------------------

    def show_geometries(
        self,
        panel_outline=None,
        free_space=None,
        substrates: list | None = None,
        partition_lines: list | None = None,
        backbone_lines: list | None = None,
        info: str = "",
    ) -> None:
        """Populate the scene and make the dock visible."""
        self._info_label.setText(info)
        self._scene.clear()

        if panel_outline is not None:
            self._add_geom(
                panel_outline,
                self._pen("#808080", style=Qt.PenStyle.DashLine),
                QBrush(Qt.BrushStyle.NoBrush),
                z=10,
            )

        if free_space is not None and not free_space.is_empty:
            self._add_geom(
                free_space,
                self._pen("#22cc55"),
                QBrush(QColor(34, 204, 85, 25)),
                z=20,
            )

        for sub in substrates or []:
            if sub is not None and not sub.is_empty:
                self._add_geom(
                    sub,
                    self._pen("#5599ff"),
                    QBrush(QColor(85, 153, 255, 40)),
                    z=30,
                )

        for geom in partition_lines or []:
            if geom is not None and not geom.is_empty:
                self._add_geom(
                    geom,
                    self._pen("#ffdd00"),
                    QBrush(Qt.BrushStyle.NoBrush),
                    z=40,
                )

        for geom in backbone_lines or []:
            if geom is not None and not geom.is_empty:
                self._add_geom(
                    geom,
                    self._pen("#ffffff", width=1.0),
                    QBrush(Qt.BrushStyle.NoBrush),
                    z=50,
                )

        if self.isVisible():
            QTimer.singleShot(50, self._fit_view)

    # ------------------------------------------------------------------

    @staticmethod
    def _pen(color: str, width: float = 0.3,
             style: Qt.PenStyle = Qt.PenStyle.SolidLine) -> QPen:
        p = QPen(QColor(color))
        p.setWidthF(width)
        p.setCosmetic(True)
        p.setStyle(style)
        return p

    def _add_geom(self, geom, pen: QPen, brush: QBrush, z: float) -> None:
        path = _shapely_to_path(geom)
        if path.isEmpty():
            return
        item = QGraphicsPathItem(path)
        item.setPen(pen)
        item.setBrush(brush)
        item.setZValue(z)
        self._scene.addItem(item)

    def _fit_view(self) -> None:
        r = self._scene.itemsBoundingRect()
        if not r.isEmpty():
            self._view.fitInView(
                r.adjusted(-2, -2, 2, 2),
                Qt.AspectRatioMode.KeepAspectRatio,
            )
