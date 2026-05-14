"""
Panel worker — runs in KiCad's Python (which has pcbnew available).

Reads a JSON payload from stdin:
    {
        "board_path":  str,
        "output_path": str,
        "config":      dict   # already through _preprocess_config + to_kikit
    }

Exits 0 on success, 1 on failure (error message written to stderr).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure src/ is on sys.path so kikit_viewer.* is importable regardless of
# how PYTHONPATH is (or isn't) inherited by this subprocess.
_src = Path(__file__).resolve().parent.parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))


def _build_type_map() -> dict:
    from kikit import panelize_ui  # type: ignore[import]
    result: dict = {}
    for section_name, fields in panelize_ui.availableSections.items():
        key = section_name.lower()
        result[key] = {fname: type(fval).__name__ for fname, fval in fields.items()}
    return result


def _build_schema_keys() -> dict:
    from kikit_viewer.config.schema import SECTIONS
    return {section: {f.key for f in fields} for section, fields in SECTIONS.items()}


def _format_section(values: dict, types: dict) -> dict:
    result = {}
    for k, v in values.items():
        field_type = types.get(k)
        if field_type is None:
            continue
        if field_type == "SLength":
            result[k] = f"{v}mm" if isinstance(v, (int, float)) else str(v)
        elif field_type == "SAngle":
            result[k] = f"{v}deg" if isinstance(v, (int, float)) else str(v)
        elif isinstance(v, bool):
            result[k] = str(v)
        else:
            result[k] = str(v)
    return result


def run(board_path: str, output_path: str, config: dict) -> None:
    from kikit import panelize_ui             # type: ignore[import]  # noqa: I001
    from kikit import panelize_ui_impl as ki  # type: ignore[import]

    type_map = _build_type_map()
    schema_keys = _build_schema_keys()

    section_overrides = {
        section: _format_section(
            {k: v for k, v in values.items() if k in schema_keys.get(section, set())},
            type_map.get(section, {}),
        )
        for section, values in config.items()
    }

    if config.get("layout", {}).get("type") == "plugin":
        section_overrides.setdefault("layout", {})
        section_overrides["layout"]["code"] = str(config["layout"].get("code", ""))
        section_overrides["layout"]["arg"]  = str(config["layout"].get("arg", ""))

    if config.get("tabs", {}).get("type") == "plugin":
        section_overrides.setdefault("tabs", {})
        section_overrides["tabs"]["code"] = str(config["tabs"].get("code", ""))
        section_overrides["tabs"]["arg"]  = str(config["tabs"].get("arg", ""))

    preset = ki.obtainPreset([], **section_overrides)

    # Redirect stdout during panelization so kikit's own print() calls don't
    # pollute the JSON we write to stdout after.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        panelize_ui.doPanelization(board_path, output_path, preset)
    finally:
        sys.stdout = real_stdout

    import pcbnew as _pcbnew  # type: ignore[import]  # noqa: I001
    from kikit_viewer.renderer.pcbnew_renderer import PcbnewSvgRenderer

    # Render the panel layers for display.
    layers = PcbnewSvgRenderer().render_layers(Path(output_path))

    # Also render the individual input board so the viewer has its size and
    # Edge_Cuts SVG without needing pcbnew itself.
    board_svgs = PcbnewSvgRenderer().render_layers(Path(board_path), ["Edge_Cuts"])
    board_edge_cuts = board_svgs.get("Edge_Cuts", "")
    try:
        _board = _pcbnew.LoadBoard(board_path)
        _bbox = _board.GetBoardEdgesBoundingBox()
        board_w = _pcbnew.ToMM(_bbox.GetWidth())
        board_h = _pcbnew.ToMM(_bbox.GetHeight())
    except Exception:
        board_w, board_h = 0.0, 0.0

    from kikit_viewer.geometry.board_outline import load_outline
    _outline = load_outline(Path(board_path))
    board_outline_pts = [list(pt) for pt in _outline.coords] if _outline is not None else []

    real_stdout.write(json.dumps({
        "svgs": layers,
        "board_edge_cuts_svg": board_edge_cuts,
        "board_w": board_w,
        "board_h": board_h,
        "board_outline_pts": board_outline_pts,
    }))
    real_stdout.flush()


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.read())
        run(
            board_path=payload["board_path"],
            output_path=payload["output_path"],
            config=payload["config"],
        )
    except Exception:
        import traceback
        sys.stderr.write(traceback.format_exc())
        sys.exit(1)
