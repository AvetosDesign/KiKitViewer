from __future__ import annotations

import math
from pathlib import Path

from shapely.affinity import scale, translate
from shapely.geometry import LinearRing, Point, Polygon
from shapely.ops import nearest_points


def load_outline(board_path: Path) -> LinearRing | None:
    """
    Load the Edge_Cuts outline of a board as a Shapely LinearRing.

    Uses pcbnew to extract ordered boundary points from the board's
    Edge_Cuts segments.  Returns None if loading fails or no closed
    outline is found.
    """
    try:
        import pcbnew  # type: ignore[import]
    except ImportError:
        return None

    try:
        board = pcbnew.LoadBoard(str(board_path))
    except Exception:
        return None

    try:
        # Attempt via KiKit's Substrate (most reliable).
        # substrate.substrates is a Shapely Polygon in KiCad internal units (nm).
        # Convert to mm by scaling, then center at origin.
        try:
            from kikit.substrate import Substrate  # type: ignore[import]

            drawings = [d for d in board.GetDrawings() if d.GetLayer() == pcbnew.Edge_Cuts]
            if drawings:
                s = Substrate(drawings)
                if not s.substrates.is_empty:
                    # Convert IU → mm and center at bounding-box centre (0, 0).
                    # Using bbox centre (not centroid) matches how LayoutPanel
                    # computes scene_cx from the board's bounding-box half-width.
                    iu_per_mm = float(pcbnew.FromMM(1))
                    poly_mm = scale(
                        s.substrates, xfact=1 / iu_per_mm, yfact=1 / iu_per_mm, origin=(0, 0)
                    )
                    minx, miny, maxx, maxy = poly_mm.bounds
                    bbox_cx = (minx + maxx) / 2.0
                    bbox_cy = (miny + maxy) / 2.0
                    poly_centered = translate(poly_mm, -bbox_cx, -bbox_cy)
                    exterior = poly_centered.exterior
                    if exterior is not None and len(exterior.coords) >= 3:
                        return exterior
        except Exception:
            pass

        # Fallback: collect Edge_Cuts segment endpoints and form a ring.
        pts: list[tuple[float, float]] = []
        for drawing in board.GetDrawings():
            layer = drawing.GetLayer()
            try:
                edge_cuts_layer = pcbnew.Edge_Cuts
            except AttributeError:
                edge_cuts_layer = 44  # Edge_Cuts layer number
            if layer != edge_cuts_layer:
                continue
            try:
                start = drawing.GetStart()
                pts.append((pcbnew.ToMM(start.x), pcbnew.ToMM(start.y)))
            except Exception:
                pass

        if len(pts) >= 3:
            try:
                poly = Polygon(LinearRing(pts))
                minx, miny, maxx, maxy = poly.bounds
                bbox_cx = (minx + maxx) / 2.0
                bbox_cy = (miny + maxy) / 2.0
                return translate(poly, -bbox_cx, -bbox_cy).exterior
            except Exception:
                pass

        return None

    except Exception:
        return None


def project_to_outline(
    outline: LinearRing,
    local_x_mm: float,
    local_y_mm: float,
    delta_mm: float = 0.01,
) -> tuple[float, float, float]:
    """
    Snap (local_x_mm, local_y_mm) to the nearest point on the outline,
    then compute the outward normal angle at that point.

    Returns (snapped_x, snapped_y, angle_deg) where:
      - snapped_x/y: mm coords of the nearest outline point (board-local)
      - angle_deg: outward normal direction in degrees
                   (0 = +X direction, 90 = +Y direction, counter-clockwise)

    The outward normal is perpendicular to the local edge tangent and
    points away from the board interior.
    """
    p = Point(local_x_mm, local_y_mm)

    # Offset side is determined by whether the LinearRing is CW or CCW
    side = "right" if outline.is_ccw else "left"

    # Create a snap line just outside the board outline.  This ensures
    # that the tab points don't end up inside the board outline.
    tabpointline = outline.parallel_offset(0.1, side)

    # Create an offset line to help us find the normal vector
    outsetline = outline.parallel_offset(1.0, side)

    # Snap our placement point to be on the snap line
    p_snap, _ = nearest_points(tabpointline, p)

    # Find the normal vector's end point using the outset line
    p_end, _ = nearest_points(outsetline, p_snap)

    nx, ny = p_end.x - p_snap.x, p_end.y - p_snap.y
    angle_deg = math.degrees(math.atan2(ny, nx))

    return p_snap.x, p_snap.y, angle_deg


def outline_from_points(pts: list) -> LinearRing | None:
    """
    Reconstruct a LinearRing from a list of [x, y] pairs (as returned by the
    panel worker). Does not require pcbnew — only Shapely.
    """
    if not pts or len(pts) < 3:
        return None
    try:
        return LinearRing(pts)
    except Exception:
        return None
