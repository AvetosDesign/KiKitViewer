from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QTransform

from kikit_viewer.config.model import ConfigModel


def content_rect_for_positions(positions: list, board_w: float, board_h: float) -> QRectF:
    """Return the rotation-aware AABB of all board placements."""
    board_rect = QRectF(-board_w / 2.0, -board_h / 2.0, board_w, board_h)
    content = QRectF()
    for p in positions:
        cx = float(p.get("x", 0.0)) + board_w / 2.0
        cy = float(p.get("y", 0.0)) + board_h / 2.0
        t = QTransform()
        t.translate(cx, cy)
        t.rotate(-float(p.get("rotation", 0.0)))
        content = content.united(t.mapRect(board_rect))
    return content if not content.isNull() else QRectF(0.0, 0.0, board_w, board_h)


def framing_offsets(
    model: ConfigModel,
    board_w: float,
    board_h: float,
    content_w: float,
    content_h: float,
) -> tuple[float, float]:
    """
    Return (ox, oy) — the panel origin offset due to framing type and minimum sizing.

    board_w/board_h: single-board dimensions (used for the base offset).
    content_w/content_h: AABB of all placed boards (used for mintotal expansion).

    KiKit expands rails symmetrically when the natural panel size falls below
    mintotalwidth/mintotalheight; that expansion is subtracted here so overlay
    items land on the actual board positions in the SVG.
    """
    try:
        frame_type = str(model.get("framing", "type"))
        frame_width = float(model.get("framing", "width"))
        frame_hspace = float(model.get("framing", "hspace"))
        frame_vspace = float(model.get("framing", "vspace"))
    except KeyError:
        frame_type, frame_width, frame_hspace, frame_vspace = "none", 0.0, 0.0, 0.0

    if frame_type in ("frame", "tightframe"):
        ox = -board_w / 2.0 - frame_hspace - frame_width
        oy = -board_h / 2.0 - frame_vspace - frame_width
    elif frame_type == "railstb":
        ox = -board_w / 2.0
        oy = -board_h / 2.0 - frame_vspace - frame_width
    elif frame_type == "railslr":
        ox = -board_w / 2.0 - frame_hspace - frame_width
        oy = -board_h / 2.0
    else:
        ox = -board_w / 2.0
        oy = -board_h / 2.0

    try:
        mintotalw = float(model.get("framing", "mintotalwidth") or 0.0)
        mintotalh = float(model.get("framing", "mintotalheight") or 0.0)
    except (KeyError, ValueError):
        return ox, oy

    if frame_type in ("frame", "tightframe"):
        nat_w = content_w + 2 * frame_hspace + 2 * frame_width
        nat_h = content_h + 2 * frame_vspace + 2 * frame_width
        ox -= max(0.0, (mintotalw - nat_w) / 2.0)
        oy -= max(0.0, (mintotalh - nat_h) / 2.0)
    elif frame_type == "railstb":
        nat_h = content_h + 2 * frame_vspace + 2 * frame_width
        oy -= max(0.0, (mintotalh - nat_h) / 2.0)
    elif frame_type == "railslr":
        nat_w = content_w + 2 * frame_hspace + 2 * frame_width
        ox -= max(0.0, (mintotalw - nat_w) / 2.0)

    return ox, oy


def panel_origin(
    model: ConfigModel,
    positions: list,
    board_w: float,
    board_h: float,
) -> tuple[float, float]:
    """
    Return (panel_ox, panel_oy) from board positions plus framing config.

    Callers are responsible for ensuring board_w/board_h are valid before calling.
    """
    content = content_rect_for_positions(positions, board_w, board_h)
    ox, oy = framing_offsets(model, board_w, board_h, content.width(), content.height())
    return ox + content.left(), oy + content.top()
