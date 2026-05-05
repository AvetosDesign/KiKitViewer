from __future__ import annotations

import json

from kikit.plugin import LayoutPlugin  # type: ignore[import]


# Populated during buildLayout; read by ManualTabsPlugin.buildTabAnnotations.
# Index i holds the rotation (degrees) of panel.substrates[i].
_substrate_rotations: list[float] = []


class TableLayoutPlugin(LayoutPlugin):
    """
    KiKit LayoutPlugin that places boards at explicit (x, y, rotation) positions.

    The positions are passed as a JSON-encoded list in the `userArg` string:
        [{"x": 10.0, "y": 5.0, "rotation": 0.0}, ...]

    All coordinates are in millimetres; rotation is in degrees.
    """

    def __init__(self, preset, userArg, netPattern, refPattern, vspace, hspace, rotation):
        super().__init__(preset, userArg, netPattern, refPattern, vspace, hspace, rotation)
        try:
            self._positions: list[dict] = json.loads(userArg) if userArg else []
        except (json.JSONDecodeError, TypeError):
            self._positions = []

    def buildLayout(self, panel, inputFile, sourceArea):
        """Place each board at its specified position and return the substrates."""
        try:
            import pcbnew
        except ImportError:
            return []

        # KiCad 9 uses VECTOR2I; older versions use wxPoint.
        _point = getattr(pcbnew, "VECTOR2I", None) or pcbnew.wxPoint

        _substrate_rotations.clear()
        substrates = []
        for pos in self._positions:
            x_nm = pcbnew.FromMM(float(pos.get("x", 0.0)))
            y_nm = pcbnew.FromMM(float(pos.get("y", 0.0)))
            angle_deg = float(pos.get("rotation", 0.0))

            # KiCad 9 uses EDA_ANGLE; older versions use tenths-of-degrees.
            if hasattr(pcbnew, "EDA_ANGLE"):
                rotation = pcbnew.EDA_ANGLE(angle_deg, pcbnew.DEGREES_T)
            else:
                rotation = int(angle_deg * 10)

            # appendBoard returns BOX2I, not Substrate — grab the newly appended
            # Substrate from panel.substrates directly.
            count_before = len(panel.substrates)
            panel.appendBoard(
                str(inputFile),
                _point(x_nm, y_nm),
                rotationAngle=rotation,
            )
            if len(panel.substrates) > count_before:
                substrates.append(panel.substrates[-1])
                _substrate_rotations.append(angle_deg)

        return substrates
