from __future__ import annotations

"""
KiKit configuration schema — sections, fields, types, and defaults.

This mirrors KiKit's own preset/config format so that the dict produced by
ConfigModel can be written directly to a .kikit.json file and consumed by the
KiKit CLI or Python API without modification.

Reference: https://yaqwsx.github.io/KiKit/latest/panelization/cli/
"""

from typing import Any

# ---------------------------------------------------------------------------
# Field descriptor
# ---------------------------------------------------------------------------

class Field:
    """Describes one parameter within a KiKit config section."""

    def __init__(
        self,
        key: str,
        label: str,
        type: str,               # "str" | "int" | "float" | "bool" | "choice"
        default: Any,
        unit: str = "",          # display unit, e.g. "mm" or "°"
        choices: list[str] | None = None,
        tooltip: str = "",
        min_val: float | None = None,
        max_val: float | None = None,
    ):
        self.key = key
        self.label = label
        self.type = type
        self.default = default
        self.unit = unit
        self.choices = choices or []
        self.tooltip = tooltip
        self.min_val = min_val
        self.max_val = max_val


# ---------------------------------------------------------------------------
# Section schemas
# ---------------------------------------------------------------------------

LAYOUT_FIELDS: list[Field] = [
    Field("type", "Layout mode", "choice", "grid",
          choices=["grid", "manual"],
          tooltip="Panelization layout algorithm"),
    Field("rows", "Rows", "int", 1, min_val=1, max_val=50),
    Field("cols", "Columns", "int", 1, min_val=1, max_val=50),
    Field("hspace", "Horizontal spacing", "float", 0.0, unit="mm",
          tooltip="Gap between board columns"),
    Field("vspace", "Vertical spacing", "float", 0.0, unit="mm",
          tooltip="Gap between board rows"),
    Field("rotation", "Rotation", "float", 0.0, unit="°",
          tooltip="Rotate each board instance by this angle"),
    Field("alternation", "Alternation", "choice", "none",
          choices=["none", "rows", "cols", "rowsCols"],
          tooltip="Mirror alternating rows/columns to reduce waste"),
    # "positions_table" type is not handled by SectionPanel — LayoutPanel manages it directly.
    Field("positions", "Board positions", "positions_table", []),
]

SOURCE_FIELDS: list[Field] = [
    Field("type", "Source type", "choice", "auto",
          choices=["auto", "rectangle", "annotation"],
          tooltip="How the board boundary is determined"),
    Field("tolerance", "Tolerance", "float", 0.1, unit="mm",
          tooltip="Extra margin when auto-detecting the board boundary"),
]

TABS_FIELDS: list[Field] = [
    Field("type", "Tab type", "choice", "spacing",
          choices=["fixed", "spacing", "corner", "full", "annotation", "manual"],
          tooltip="How tabs are placed along board edges"),
    Field("hwidth", "H tab width", "float", 3.0, unit="mm",
          tooltip="Width of tabs on horizontal edges"),
    Field("vwidth", "V tab width", "float", 3.0, unit="mm",
          tooltip="Width of tabs on vertical edges"),
    Field("hcount", "Tabs per horizontal edge", "int", 1, min_val=0, max_val=20),
    Field("vcount", "Tabs per vertical edge", "int", 1, min_val=0, max_val=20),
    Field("spacing", "Tab spacing", "float", 10.0, unit="mm",
          tooltip="Distance between tabs (used with type=spacing)"),
    Field("mindistance", "Min tab distance", "float", 0.0, unit="mm",
          tooltip="Minimum distance from board corners"),
    Field("fillet", "Tab fillet", "float", 0.0, unit="mm"),
    # "tab_list" type is not forwarded to KiKit — handled by runner translation.
    Field("positions", "Tab positions", "tab_list", []),
]

CUTS_FIELDS: list[Field] = [
    Field("type", "Cut type", "choice", "mousebites",
          choices=["mousebites", "vcuts", "layer", "annotation", "fixed"],
          tooltip="How boards are separated after depanelization"),
    Field("drill", "Drill diameter", "float", 0.5, unit="mm",
          tooltip="Drill hole diameter for mouse bites"),
    Field("spacing", "Hole spacing", "float", 0.8, unit="mm",
          tooltip="Center-to-center spacing between mouse bite holes"),
    Field("offset", "Offset", "float", 0.0, unit="mm",
          tooltip="Offset of cuts from the board edge"),
    Field("prolong", "Prolong", "float", 0.0, unit="mm",
          tooltip="Extend cuts beyond the tab edges"),
    Field("cutcurves", "Cut curves", "bool", False,
          tooltip="Generate cuts for curved edges (mouse bites only)"),
]

FRAMING_FIELDS: list[Field] = [
    Field("type", "Frame type", "choice", "none",
          choices=["none", "frame", "railstb", "railslr", "tightframe"],
          tooltip="Panel frame or rails around the boards"),
    Field("width", "Frame width", "float", 5.0, unit="mm"),
    Field("hspace", "Horizontal margin", "float", 2.0, unit="mm",
          tooltip="Gap between outermost boards and frame (horizontal)"),
    Field("vspace", "Vertical margin", "float", 2.0, unit="mm",
          tooltip="Gap between outermost boards and frame (vertical)"),
    Field("cuts", "Frame cuts", "choice", "none",
          choices=["none", "both", "v", "h"],
          tooltip="V-cuts through the frame for easier separation"),
    Field("chamferwidth", "Chamfer width", "float", 0.0, unit="mm",
          tooltip="Frame corner chamfer size (horizontal)"),
    Field("chamferheight", "Chamfer height", "float", 0.0, unit="mm",
          tooltip="Frame corner chamfer size (vertical)"),
    Field("fillet", "Fillet radius", "float", 0.0, unit="mm"),
    Field("mintotalheight", "Min total height", "float", 0.0, unit="mm"),
    Field("mintotalwidth", "Min total width", "float", 0.0, unit="mm"),
]

TOOLING_FIELDS: list[Field] = [
    Field("type", "Tooling type", "choice", "none",
          choices=["none", "3hole", "4hole"],
          tooltip="Tooling holes for pick-and-place or jigs"),
    Field("hoffset", "Horizontal offset", "float", 2.5, unit="mm"),
    Field("voffset", "Vertical offset", "float", 2.5, unit="mm"),
    Field("size", "Hole diameter", "float", 2.0, unit="mm"),
    Field("paste", "Paste on holes", "bool", False),
]

FIDUCIALS_FIELDS: list[Field] = [
    Field("type", "Fiducial type", "choice", "none",
          choices=["none", "3fid", "4fid"],
          tooltip="Fiducial markers for SMT assembly alignment"),
    Field("hoffset", "Horizontal offset", "float", 5.0, unit="mm"),
    Field("voffset", "Vertical offset", "float", 2.5, unit="mm"),
    Field("coppersize", "Copper diameter", "float", 1.0, unit="mm"),
    Field("opening", "Mask opening", "float", 2.0, unit="mm"),
    Field("paste", "Paste on fiducials", "bool", False),
]

TEXT_FIELDS: list[Field] = [
    Field("type", "Text type", "choice", "none",
          choices=["none", "simple"],
          tooltip="Annotation text printed on the panel"),
    Field("text", "Text content", "str", ""),
    Field("anchor", "Anchor corner", "choice", "tl",
          choices=["tl", "tr", "bl", "br"]),
    Field("hoffset", "Horizontal offset", "float", 0.0, unit="mm"),
    Field("voffset", "Vertical offset", "float", 0.0, unit="mm"),
    Field("layer", "Layer", "str", "F_Cu"),
    Field("width", "Text width", "float", 1.5, unit="mm"),
    Field("height", "Text height", "float", 1.5, unit="mm"),
    Field("thickness", "Line thickness", "float", 0.3, unit="mm"),
    Field("hjustify", "H-justify", "choice", "left",
          choices=["left", "center", "right"]),
    Field("vjustify", "V-justify", "choice", "top",
          choices=["top", "center", "bottom"]),
]

POST_FIELDS: list[Field] = [
    Field("type", "Post type", "choice", "auto",
          choices=["auto"]),
    Field("millradius", "Mill fillet radius", "float", 1.0, unit="mm",
          tooltip="Round internal corners to this radius for milling"),
    Field("copperfill", "Copper fill", "bool", False,
          tooltip="Fill panel frame with copper"),
    Field("reconstructarcs", "Reconstruct arcs", "bool", False),
    Field("refillzones", "Refill zones", "bool", False),
    Field("edgewidth", "Edge width", "float", 0.1, unit="mm"),
]

PAGE_FIELDS: list[Field] = [
    Field("type", "Page size", "choice", "inherit",
          choices=["inherit", "A0", "A1", "A2", "A3", "A4", "A5",
                   "letter", "legal", "ledger"],
          tooltip="Output page size for the panel PCB"),
    Field("anchor", "Board anchor", "choice", "tl",
          choices=["tl", "tr", "bl", "br"]),
]

# ---------------------------------------------------------------------------
# Section registry
# ---------------------------------------------------------------------------

SECTIONS: dict[str, list[Field]] = {
    "layout":    LAYOUT_FIELDS,
    "source":    SOURCE_FIELDS,
    "framing":   FRAMING_FIELDS,
    "tabs":      TABS_FIELDS,
    "cuts":      CUTS_FIELDS,
    "tooling":   TOOLING_FIELDS,
    "fiducials": FIDUCIALS_FIELDS,
    "text":      TEXT_FIELDS,
    "post":      POST_FIELDS,
    "page":      PAGE_FIELDS,
}


def defaults() -> dict[str, dict[str, Any]]:
    """Return a dict of KiKit config defaults for all sections."""
    return {
        section: {f.key: f.default for f in fields}
        for section, fields in SECTIONS.items()
    }
