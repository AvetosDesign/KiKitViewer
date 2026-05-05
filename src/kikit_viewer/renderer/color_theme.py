from __future__ import annotations

import json
import os
import re
from pathlib import Path

from kikit_viewer.renderer.pcbnew_renderer import _LAYER_COLORS as _FALLBACK_COLORS

# Maps KiCad theme JSON keys → our canonical layer names.
# Copper inner layers are handled separately via the "copper" sub-dict.
_THEME_KEY_TO_LAYER: dict[str, str] = {
    "f_silks":   "F_Silkscreen",
    "b_silks":   "B_Silkscreen",
    "edge_cuts": "Edge_Cuts",
    "f_mask":    "F_Mask",
    "b_mask":    "B_Mask",
    "f_paste":   "F_Paste",
    "b_paste":   "B_Paste",
    "f_fab":     "F_Fab",
    "b_fab":     "B_Fab",
    "f_crtyd":   "F_Courtyard",
    "b_crtyd":   "B_Courtyard",
}

_COPPER_KEY_TO_LAYER: dict[str, str] = {
    "f":   "F_Cu",
    "b":   "B_Cu",
    **{f"in{i}": f"In{i}_Cu" for i in range(1, 31)},
}


def load_layer_colors() -> dict[str, str]:
    """
    Return {layer_name: hex_color} by reading the active pcbnew color theme.

    Resolution order:
      1. pcbnew.json  → .appearance.color_theme  → theme name
      2. User colors dir  → <theme>.json
      3. System colors dir → <theme>.json
      4. Fall back to hardcoded defaults for any missing layer

    Always returns a complete dict — missing layers get their hardcoded default.
    """
    colors = dict(_FALLBACK_COLORS)  # start with defaults

    try:
        theme_colors = _read_active_theme()
        colors.update(theme_colors)
    except Exception:
        pass  # any failure → keep defaults

    return colors


def _read_active_theme() -> dict[str, str]:
    """Load and parse the active pcbnew color theme. Raises on any failure."""
    theme_name = _active_theme_name()
    theme_path = _find_theme_file(theme_name)
    board_colors = json.loads(theme_path.read_text(encoding="utf-8"))["board"]
    return _parse_board_colors(board_colors)


def _active_theme_name() -> str:
    appdata = os.environ.get("APPDATA", "")
    pcbnew_json = Path(appdata) / "kicad" / "9.0" / "pcbnew.json"
    data = json.loads(pcbnew_json.read_text(encoding="utf-8"))
    return data["appearance"]["color_theme"]


def _find_theme_file(name: str) -> Path:
    appdata = os.environ.get("APPDATA", "")
    candidates = [
        Path(appdata) / "kicad" / "9.0" / "colors" / f"{name}.json",
        Path("C:/Program Files/KiCad/9.0/share/kicad/colors") / f"{name}.json",
    ]
    for path in candidates:
        if path.exists():
            return path
    raise FileNotFoundError(f"KiCad color theme '{name}' not found")


def _parse_board_colors(board: dict) -> dict[str, str]:
    result: dict[str, str] = {}

    for theme_key, layer_name in _THEME_KEY_TO_LAYER.items():
        raw = board.get(theme_key)
        if raw:
            hex_color = _to_hex(raw)
            if hex_color:
                result[layer_name] = hex_color

    copper = board.get("copper", {})
    for copper_key, layer_name in _COPPER_KEY_TO_LAYER.items():
        raw = copper.get(copper_key)
        if raw:
            hex_color = _to_hex(raw)
            if hex_color:
                result[layer_name] = hex_color

    return result


def _to_hex(color: str) -> str | None:
    """Convert 'rgb(r, g, b)' or 'rgba(r, g, b, a)' to '#RRGGBB'."""
    m = re.match(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", color)
    if not m:
        return None
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    return f"#{r:02X}{g:02X}{b:02X}"
