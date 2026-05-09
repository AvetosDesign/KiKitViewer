from __future__ import annotations

import json
import math

import numpy as np
import pcbnew as _pcbnew  # type: ignore[import]
from kikit.plugin import TabsPlugin  # type: ignore[import]
from shapely import make_valid, set_precision
from shapely.geometry import LineString, MultiLineString, MultiPoint, MultiPolygon, Point, Polygon
from shapely.ops import unary_union, voronoi_diagram

# Populated at the end of each _build_partition_lines call (coordinates in mm).
# Read by MainWindow._on_run_finished to update the debug dock.
_last_debug_geometries: dict = {}


def _grid_substrate_rotation(substrate_idx: int, preset) -> float:
    """Return the effective rotation (degrees) for a substrate in KiKit's native grid layout.

    KiKit's TableLayoutPlugin populates _substrate_rotations for manual layout;
    for grid layout the list is empty, so we calculate the rotation from the
    alternation setting in the model.
    """
    try:
        alternation = preset["layout"]["alternation"]
    except (KeyError, TypeError):
        alternation = "none"

    if alternation == "none":
        return 0.0

    try:
        cols = int(preset["layout"]["cols"])
    except (KeyError, TypeError, ValueError):
        cols = 1

    col = substrate_idx % cols
    row = substrate_idx // cols

    if alternation == "cols" and col % 2 == 1:
        return 180.0
    if alternation == "rows" and row % 2 == 1:
        return 180.0
    if alternation == "rowsCols" and (col + row) % 2 == 1:
        return 180.0
    return 0.0


class ManualTabsPlugin(TabsPlugin):
    """
    KiKit TabsPlugin that places tabs at explicit board-local positions.

    Positions are passed as a JSON-encoded list in the `userArg` string:
        [{"x": 5.0, "y": 0.0, "a": 90.0}, ...]

    Each entry describes one tab:
      x, y : mm offset from the board centroid (board-local, before rotation)
      a    : outward normal angle in degrees (0 = +X, 90 = +Y, CCW positive)

    Because all boards in the panel are copies of the same source board, the
    same set of positions is applied to every substrate.

    Partition polygons are computed via a Voronoi diagram seeded from substrate
    perimeter samples, which correctly handles non-convex boards (where centroid-
    based seeding produces wrong territory boundaries).
    """

    def __init__(self, preset, userArg):
        super().__init__(preset, userArg)
        try:
            self._positions: list[dict] = json.loads(userArg) if userArg else []
        except (json.JSONDecodeError, TypeError):
            self._positions = []

    def buildTabAnnotations(self, panel) -> None:
        """Attach TabAnnotation objects to each substrate and set partition lines."""
        try:
            import pcbnew  # type: ignore[import]
        except ImportError:
            return

        _point = getattr(pcbnew, "VECTOR2I", None) or pcbnew.wxPoint

        try:
            from kikit.annotations import TabAnnotation  # type: ignore[import]
        except ImportError:
            return

        # Tab width: prefer hwidth from the preset; fall back to 5 mm.
        try:
            tab_width = self.preset["tabs"]["hwidth"]
        except (KeyError, TypeError):
            tab_width = pcbnew.FromMM(5.0)

        partition_lines, backbone_lines = _build_partition_lines(panel, self.preset)

        for i, substrate in enumerate(panel.substrates):
            # Use the bounding-box centre, not the geometric centroid.
            # (For asymmetric boards the centroid diverges from the bbox centre.)
            # load_outline() centres the board outline at its bbox centre, so
            # our stored (x_mm, y_mm) offsets are relative to that same point.
            minx, miny, maxx, maxy = substrate.substrates.bounds
            cx_iu = (minx + maxx) / 2.0  # BB center x location
            cy_iu = (miny + maxy) / 2.0  # BB center y location

            try:
                from kikit_viewer.plugins.table_layout import _substrate_rotations

                if i < len(_substrate_rotations):
                    subrot_deg = float(_substrate_rotations[i])
                else:
                    # Grid layout mode: _substrate_rotations is empty because
                    # TableLayoutPlugin was not used.  Derive rotation from the
                    # alternation setting and column count instead.
                    subrot_deg = _grid_substrate_rotation(i, self.preset)
            except (ImportError, IndexError, TypeError, ValueError):
                subrot_deg = 0.0

            for pos in self._positions:
                # Extract tab position and angle (pre-init x_mm and y_mm)
                br_x_mm = float(pos.get("x", 0.0))  # Board-relative x
                br_y_mm = float(pos.get("y", 0.0))  # Board-relative y
                br_a_deg = float(pos.get("a", 0.0))  # Board-relative angle

                if subrot_deg:
                    # KiCad appendBoard() is CCW-positive on screen; standard math
                    # CCW formula needs negation to produce that same transform.
                    rot_rad = math.radians(-subrot_deg)
                    cos_r, sin_r = math.cos(rot_rad), math.sin(rot_rad)
                    x_mm = br_x_mm * cos_r - br_y_mm * sin_r
                    y_mm = br_x_mm * sin_r + br_y_mm * cos_r
                    a_deg = (br_a_deg - subrot_deg) % 360
                else:
                    x_mm, y_mm, a_deg = br_x_mm, br_y_mm, br_a_deg

                # Translate the board-relative coords to panel-relative
                origin_x = int(cx_iu + pcbnew.FromMM(x_mm))  # Tab's panel x loc
                origin_y = int(cy_iu + pcbnew.FromMM(y_mm))  # Tab's panel y loc
                origin = _point(origin_x, origin_y)

                # substrate.tab() expects the inward normal; angle_deg is outward, so negate.
                nx = -math.cos(math.radians(a_deg))
                ny = -math.sin(math.radians(a_deg))

                # Add the annotation to the list
                substrate.annotations.append(TabAnnotation(None, origin, (nx, ny), tab_width))

            if i < len(partition_lines) and partition_lines[i] is not None:
                pl = partition_lines[i]
                # KiKit expects a LineString/LinearRing, not a Polygon.
                # Pass the exterior ring so isLinestringCyclic can access .coords.
                substrate.partitionLine = pl.exterior if hasattr(pl, "exterior") else pl

        # Replace bounding-box backbone lines from the layout phase with lines
        # derived from our Voronoi partition boundaries.
        panel.backboneLines.clear()
        panel.backboneLines.extend(backbone_lines)

    def buildTabs(self, panel):
        """
        Build tabs and backbone strip material.

        The inherited default calls buildTabAnnotations then
        buildTabsFromAnnotations.  We override to insert backbone strip
        generation between the two: tabs from adjacent boards meet exactly at
        the Voronoi partition boundary (zero overlap), so without a physical
        strip at that boundary they have nothing to attach to.  The strip is
        buffered from each backbone LineString by half the tab width and unioned
        into boardSubstrate before tabs are built.
        """
        self.buildTabAnnotations(panel)
        _render_backbone_strips(panel, self.preset)

        # Normalize board substrate precision before tab union so that backbone
        # strip coordinates (from floating-point buffer ops) don't leave residuals.
        panel.boardSubstrate.substrates = set_precision(
            make_valid(panel.boardSubstrate.substrates), 1.0
        )

        try:
            fillet = self.preset["tabs"]["fillet"]
        except (KeyError, TypeError):
            fillet = 0

        # KiKit computes tab rectangles via cos/sin, producing fractional coords
        # that can trigger GEOS topology exceptions when unioned with the substrate.
        # Shadow boardSubstrate.union temporarily to normalize each incoming tab.
        _orig_union = panel.boardSubstrate.union
        def _safe_union(other):
            _orig_union([
                set_precision(make_valid(g), 1.0) if hasattr(g, "geom_type") else g
                for g in other
            ])
        panel.boardSubstrate.union = _safe_union
        try:
            return panel.buildTabsFromAnnotations(fillet)
        finally:
            del panel.boardSubstrate.union


def _render_backbone_strips(panel, preset) -> None:
    """
    Buffer each Voronoi backbone LineString by half the tab width and union
    the resulting strips into panel.boardSubstrate.

    This creates the physical bridge material between adjacent boards so that
    tabs from both sides have something to attach to.

    Skipped for tightframe framing: the frame itself provides the material
    that the tabs attach to, so no separate backbone strip is needed.
    """
    try:
        if preset["framing"]["type"] == "tightframe":
            return
    except (KeyError, TypeError):
        pass

    try:
        tab_width = preset["tabs"]["hwidth"]
    except (KeyError, TypeError):
        tab_width = _pcbnew.FromMM(5.0)

    half_w = tab_width // 2  # type: ignore[operator]  # pcbnew IU int
    pieces = []
    for bl in panel.backboneLines:
        if isinstance(bl, (LineString, MultiLineString)) and not bl.is_empty:
            strip = make_valid(bl.buffer(half_w, cap_style="flat"))
            if not strip.is_empty:
                pieces.append(strip)

    if pieces:
        panel.backbonePieces = (getattr(panel, "backbonePieces", None) or []) + pieces
        panel.boardSubstrate.union(pieces)
        # Normalize board substrate after adding strips so subsequent unions
        # (buildTabsFromAnnotations) start from topologically clean geometry.
        panel.boardSubstrate.substrates = make_valid(panel.boardSubstrate.substrates)


def _build_partition_lines(panel, preset=None) -> tuple[list, list[LineString]]:
    """
    Compute one Voronoi territory polygon per substrate.

    Seeds the Voronoi diagram from points sampled along each substrate's
    perimeter rather than from centroids, so non-convex board shapes produce
    correct territory boundaries.  The envelope is estimated from the substrate
    union buffered by frame_width + routing_bit_diameter so that tabs at the
    panel edge are bounded to the frame interior.

    Returns a list parallel to panel.substrates.
    """
    all_boards = unary_union([s.substrates for s in panel.substrates])

    # Estimate the panel perimeter from the substrate union offset by the
    # board-to-frame spacing, as established by the milling bit diameter.
    # That is the distance a tab at the panel edge must travel to reach the
    # frame, so it bounds the territory polygon correctly without
    # over-extending into the frame material. Fall back to the frame width,
    # and finally to a fixed 5 mm.
    margin = _pcbnew.FromMM(5.0)  # safe fallback
    if preset is not None:
        try:
            margin = 2.0 * float(preset["post"]["millradius"]) + 1.0
        except (KeyError, TypeError):
            try:
                margin = float(preset["framing"]["width"])
            except (KeyError, TypeError):
                pass
    envelope = all_boards.envelope.buffer(margin)

    # Sample points from each substrate's perimeter.
    tolerance = _pcbnew.FromMM(1.0)
    all_coords: list[tuple[float, float]] = []
    point_owner: list[int] = []

    for i, substrate in enumerate(panel.substrates):
        geom = substrate.substrates
        rings = []
        if isinstance(geom, Polygon):
            rings = [geom.exterior]
        elif isinstance(geom, MultiPolygon):
            rings = [g.exterior for g in geom.geoms]
        for ring in rings:
            dists = np.arange(0, ring.length, tolerance)
            for d in dists:
                p = ring.interpolate(d)
                all_coords.append((p.x, p.y))
                point_owner.append(i)

    if len(all_coords) < 2:
        return [envelope] * len(panel.substrates), []

    # Build Voronoi from perimeter sample points.
    vd = voronoi_diagram(MultiPoint(all_coords), envelope=envelope)

    # Assign each Voronoi region to the substrate that owns the sample point
    # inside that region, then union all regions per substrate.
    regions_per_substrate: list[list] = [[] for _ in panel.substrates]
    for region in vd.geoms:
        for j, (px, py) in enumerate(all_coords):
            if region.contains(Point(px, py)):
                regions_per_substrate[point_owner[j]].append(region)
                break

    result = [
        set_precision(make_valid(unary_union(regions).intersection(envelope)), 1.0)
        if regions else set_precision(make_valid(envelope), 1.0)
        for regions in regions_per_substrate
    ]

    # Extract inter-substrate boundaries as backbone lines.
    backbone_lines: list[LineString] = []
    for i in range(len(result)):
        for j in range(i + 1, len(result)):
            a, b = result[i], result[j]
            if a is None or b is None:
                continue
            shared = a.intersection(b)
            if shared.is_empty:
                continue
            if isinstance(shared, LineString):
                backbone_lines.append(shared)
            elif isinstance(shared, MultiLineString):
                backbone_lines.extend(shared.geoms)

    # Capture geometries in mm for the debug dock.
    try:
        from shapely import affinity as _affinity

        _iu_per_mm = float(_pcbnew.FromMM(1.0))

        def _to_mm(g):
            return _affinity.scale(g, xfact=1.0 / _iu_per_mm, yfact=1.0 / _iu_per_mm, origin=(0, 0))

        def _raw(section, key):
            try:
                return f"{float(preset[section][key]) / _iu_per_mm:.2f} mm"
            except Exception:
                return "n/a"

        _info = (
            f"substrates:       {len(panel.substrates)}\n"
            f"all_boards:       {all_boards.geom_type}  "
            f"area={all_boards.area / _iu_per_mm**2:.1f} mm²\n"
            f"layout.hspace:    {_raw('layout', 'hspace')}\n"
            f"layout.vspace:    {_raw('layout', 'vspace')}\n"
            f"framing.width:    {_raw('framing', 'width')}\n"
            f"margin (used):    {margin / _iu_per_mm:.2f} mm\n"
            f"sample points:    {len(all_coords)}\n"
            f"partition geoms:  {len(result)}\n"
            f"backbone lines:   {len(backbone_lines)}"
        )
        _last_debug_geometries.clear()
        _last_debug_geometries.update(
            {
                "panel_outline": _to_mm(envelope),
                "free_space": _to_mm(envelope.difference(all_boards)),
                "substrates": [_to_mm(s.substrates) for s in panel.substrates],
                "partition_lines": [_to_mm(r) for r in result if r is not None],
                "backbone_lines": [_to_mm(bl) for bl in backbone_lines],
                "info": _info,
            }
        )
    except Exception as _e:
        _last_debug_geometries["info"] = f"geometry capture failed: {_e}"

    return result, backbone_lines
