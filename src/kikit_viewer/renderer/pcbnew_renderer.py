from __future__ import annotations

import re
import tempfile
from pathlib import Path

_PCBNEW_MISSING_MSG = (
    "pcbnew module not found. "
    "Ensure KiCad is installed and its Python scripting path is on sys.path. "
    "On Windows, add C:\\Program Files\\KiCad\\<version>\\bin to sys.path."
)

# Default display colors for standard KiCad layers
_LAYER_COLORS: dict[str, str] = {
    "F_Cu": "#B87333",
    "B_Cu": "#4D7FC4",
    "In1_Cu":  "#FFBF00",
    "In2_Cu":  "#BF7F00",
    "In3_Cu":  "#FFA500",
    "In4_Cu":  "#FF8C00",
    "In5_Cu":  "#FF6600",
    "In6_Cu":  "#FF4500",
    "In7_Cu":  "#FFD700",
    "In8_Cu":  "#DAA520",
    "In9_Cu":  "#B8860B",
    "In10_Cu": "#CD853F",
    "In11_Cu": "#D2691E",
    "In12_Cu": "#A0522D",
    "In13_Cu": "#8B4513",
    "In14_Cu": "#6B8E23",
    "In15_Cu": "#556B2F",
    "In16_Cu": "#8FBC8F",
    "In17_Cu": "#2E8B57",
    "In18_Cu": "#3CB371",
    "In19_Cu": "#20B2AA",
    "In20_Cu": "#008B8B",
    "In21_Cu": "#4682B4",
    "In22_Cu": "#6495ED",
    "In23_Cu": "#7B68EE",
    "In24_Cu": "#9370DB",
    "In25_Cu": "#8A2BE2",
    "In26_Cu": "#9400D3",
    "In27_Cu": "#BA55D3",
    "In28_Cu": "#FF69B4",
    "In29_Cu": "#FF1493",
    "In30_Cu": "#DB7093",
    "Edge_Cuts": "#FFFF00",
    "F_Silkscreen": "#F2F2F2",
    "B_Silkscreen": "#7BC8F6",
    "F_Fab": "#808080",
    "B_Fab": "#808080",
    "F_Courtyard": "#FF00FF",
    "B_Courtyard": "#FF00FF",
    "F_Mask": "#800000",
    "B_Mask": "#000080",
    "F_Paste": "#BFBFBF",
    "B_Paste": "#BFBFBF",
}

# Maps canonical layer names → possible pcbnew module attribute names.
# Multiple entries handle KiCad version differences (e.g. F_SilkS vs F_Silkscreen).
_LAYER_PCBNEW_ATTRS: dict[str, list[str]] = {
    "F_Cu":         ["F_Cu"],
    "B_Cu":         ["B_Cu"],
    "Edge_Cuts":    ["Edge_Cuts"],
    "F_Silkscreen": ["F_Silkscreen", "F_SilkS"],
    "B_Silkscreen": ["B_Silkscreen", "B_SilkS"],
    "F_Fab":        ["F_Fab"],
    "B_Fab":        ["B_Fab"],
    "F_Courtyard":  ["F_CrtYd"],
    "B_Courtyard":  ["B_CrtYd"],
    "F_Mask":       ["F_Mask"],
    "B_Mask":       ["B_Mask"],
    "F_Paste":      ["F_Paste"],
    "B_Paste":      ["B_Paste"],
}


class PcbnewSvgRenderer:
    """
    Renders a .kicad_pcb file to per-layer SVG strings using pcbnew's PLOT_CONTROLLER.

    No running KiCad instance is required. SVG strings are suitable for use with
    Qt's QSvgRenderer.
    """

    DEFAULT_LAYERS = [
        "Edge_Cuts",
        "F_Cu",
        "B_Cu",
        "F_Fab",
        "B_Fab",
        "F_Silkscreen",
        "B_Silkscreen",
    ]

    DEFAULT_LAYER_COLORS = _LAYER_COLORS

    def render_layers(
        self,
        board_path: Path,
        layers: list[str] | None = None,
    ) -> dict[str, str]:
        """
        Render a board to per-layer SVG strings.

        Returns {layer_name: svg_content} for each successfully rendered layer.
        Layers absent from the board are silently skipped.
        """
        try:
            import pcbnew
        except ImportError as exc:
            raise ImportError(_PCBNEW_MISSING_MSG) from exc

        board = pcbnew.LoadBoard(str(board_path))
        plot_fmt = _get_plot_format_svg(pcbnew)

        if layers is None:
            layers = _layers_for_board(board)

        bbox = board.GetBoardEdgesBoundingBox()
        bx = pcbnew.ToMM(bbox.GetX())
        by = pcbnew.ToMM(bbox.GetY())
        bw = pcbnew.ToMM(bbox.GetWidth())
        bh = pcbnew.ToMM(bbox.GetHeight())
        has_valid_bounds = bw >= 0.1 and bh >= 0.1

        result: dict[str, str] = {}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            pctl = pcbnew.PLOT_CONTROLLER(board)
            popt = pctl.GetPlotOptions()
            popt.SetOutputDirectory(tmp)
            popt.SetPlotFrameRef(False)
            popt.SetScale(1.0)
            popt.SetMirror(False)
            _try_opt(popt, "SetAutoScale", False)
            _try_opt(popt, "SetExcludeEdgeLayer", False)
            _try_opt(popt, "SetPlotInvisibleText", False)
            # Show full drill-hole circles on copper layers so NPTH tooling holes
            # and other drills are visible even without a copper pour nearby.
            _try_opt(popt, "SetDrillMarksType",
                     getattr(pcbnew, "DRILL_MARKS_FULL_DRILL_SHAPE", 2))

            # KiCad 9 renamed OpenPlotfileName → OpenPlotfile(suffix, format).
            use_new_api = hasattr(pctl, "OpenPlotfile")

            for layer_name in layers:
                layer_id = _get_layer_id(pcbnew, board, layer_name)
                if layer_id is None:
                    continue

                existing = set(tmp_path.glob("*.svg"))
                pctl.SetLayer(layer_id)

                if use_new_api:
                    ok = pctl.OpenPlotfile(layer_name, plot_fmt)
                else:
                    file_base = str(tmp_path / f"svg_{layer_name}")
                    ok = pctl.OpenPlotfileName(file_base, plot_fmt)

                if not ok:
                    continue
                pctl.PlotLayer()
                pctl.ClosePlot()

                new_files = set(tmp_path.glob("*.svg")) - existing
                if new_files:
                    svg = next(iter(new_files)).read_text(encoding="utf-8")
                    if has_valid_bounds:
                        svg = _crop_svg_to_board(svg, bx, by, bw, bh)
                    result[layer_name] = svg

        return result

    def get_board_bounds_mm(self, board_path: Path) -> tuple[float, float, float, float]:
        """Return (x_mm, y_mm, width_mm, height_mm) of the board's Edge_Cuts bounding box."""
        try:
            import pcbnew
        except ImportError as exc:
            raise ImportError(_PCBNEW_MISSING_MSG) from exc

        board = pcbnew.LoadBoard(str(board_path))
        bbox = board.GetBoardEdgesBoundingBox()
        return (
            pcbnew.ToMM(bbox.GetX()),
            pcbnew.ToMM(bbox.GetY()),
            pcbnew.ToMM(bbox.GetWidth()),
            pcbnew.ToMM(bbox.GetHeight()),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _layers_for_board(board) -> list[str]:
    """Build the ordered render layer list from the board's actual copper stack."""
    copper_count = board.GetCopperLayerCount()
    inner = [f"In{i}_Cu" for i in range(1, copper_count - 1)]
    return (
        ["Edge_Cuts", "F_Cu"]
        + inner
        + ["B_Cu", "F_Fab", "B_Fab", "F_Silkscreen", "B_Silkscreen"]
    )


def _get_layer_id(pcbnew, board, layer_name: str) -> int | None:
    """Resolve a canonical layer name to a pcbnew layer ID integer."""
    for attr in _LAYER_PCBNEW_ATTRS.get(layer_name, [layer_name]):
        value = getattr(pcbnew, attr, None)
        if value is not None:
            return int(value)

    undefined = getattr(pcbnew, "UNDEFINED_LAYER", -1)
    layer_id = board.GetLayerID(layer_name)
    if layer_id != undefined:
        return layer_id
    return None


def _get_plot_format_svg(pcbnew) -> int:
    """Return the SVG plot format constant, tolerating API changes across KiCad versions."""
    fmt = getattr(pcbnew, "PLOT_FORMAT_SVG", None)
    if fmt is not None:
        return fmt
    plot_format = getattr(pcbnew, "PLOT_FORMAT", None)
    if plot_format is not None:
        svg = getattr(plot_format, "SVG", None)
        if svg is not None:
            return svg
    raise RuntimeError("Cannot locate PLOT_FORMAT_SVG in the pcbnew module.")


def _try_opt(popt, method: str, value) -> None:
    """Call a PLOT_OPTIONS setter only if it exists (handles KiCad version differences)."""
    fn = getattr(popt, method, None)
    if fn is not None:
        try:
            fn(value)
        except Exception:
            pass


def _crop_svg_to_board(svg: str, bx: float, by: float, bw: float, bh: float) -> str:
    """
    Replace the SVG canvas (full drawing sheet) with a tight crop to the board area.

    pcbnew plots to the active drawing-sheet size (e.g., A4) with the board at its
    absolute KiCad coordinates. This resets width, height, and viewBox so only the
    board bounding box is visible, making the SVG align with scene mm coordinates.
    """
    svg = re.sub(r'viewBox="[^"]*"', f'viewBox="{bx:.4f} {by:.4f} {bw:.4f} {bh:.4f}"', svg)
    svg = re.sub(r'width="[\d.]+mm"', f'width="{bw:.4f}mm"', svg)
    svg = re.sub(r'height="[\d.]+mm"', f'height="{bh:.4f}mm"', svg)
    return svg
