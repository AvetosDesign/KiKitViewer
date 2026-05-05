from __future__ import annotations

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any


from PySide6.QtCore import QThread, Signal


class KiKitRunner(QThread):
    """
    Runs KiKit panelization in a background thread.

    Accepts a snapshot of the current config dict and board path, then calls
    the KiKit Python API to produce a panel .kicad_pcb.

    Signals
    -------
    finished : Path
        Emitted on success with the path to the output .kicad_pcb.
    failed : str
        Emitted on failure with a human-readable error message.
    """

    finished = Signal(Path, dict)
    failed = Signal(str)

    def __init__(
        self,
        board_path: Path,
        config: dict[str, Any],
        output_path: Path,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._board_path = board_path
        self._config = config
        self._output_path = output_path

    def run(self) -> None:
        try:
            if self._nothing_to_panelize():
                self.finished.emit(self._output_path, {})
                return
            svgs = self._invoke_kikit()
            self.finished.emit(self._output_path, svgs)
        except Exception as exc:
            self.failed.emit(_format_error(exc))

    def _nothing_to_panelize(self) -> bool:
        """Return True when the config would give KiKit zero boards to place."""
        layout = self._config.get("layout", {})
        return layout.get("type") == "manual" and not layout.get("positions", [])

    def _invoke_kikit(self) -> dict:
        from kikit_viewer.config.translation import to_kikit
        kikit_config = _preprocess_config(to_kikit(self._config))

        worker = Path(__file__).parent / "panel_worker.py"
        payload = json.dumps({
            "board_path":  str(self._board_path),
            "output_path": str(self._output_path),
            "config":      kikit_config,
        })

        # Run panelization in KiCad's Python (pcbnew + kikit both native there via PCM).
        # Only src/ needs to be on PYTHONPATH for kikit_viewer.plugins.*.
        kicad_python = os.environ.get("KICAD_PYTHON", sys.executable)
        src_dir = str(Path(__file__).parent.parent.parent)
        pythonpath = src_dir

        env = os.environ.copy()
        env.pop("PYTHONHOME", None)
        env["PYTHONPATH"] = pythonpath

        result = subprocess.run(
            [kicad_python, str(worker)],
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            creationflags=0x08000000 if sys.platform == "win32" else 0,  # CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or "panel_worker exited with no output")

        try:
            return json.loads(result.stdout)
        except Exception:
            return {}


def _preprocess_config(config: dict) -> dict:
    """
    Translate KiKitViewer-specific types into KiKit-native equivalents.

    Handles:
      layout "manual" → "plugin" + TableLayoutPlugin + JSON-encoded positions
      tabs   "manual" → "plugin" + ManualTabsPlugin  + JSON-encoded positions
    """
    import copy
    import json as _json
    config = copy.deepcopy(config)
    layout = config.get("layout", {})
    if layout.get("type") == "manual":
        positions = layout.pop("positions", [])
        layout["type"] = "plugin"
        layout["code"] = "kikit_viewer.plugins.table_layout.TableLayoutPlugin"
        layout["arg"] = _json.dumps(positions)

    tabs = config.get("tabs", {})
    if tabs.get("type") == "manual":
        positions = tabs.pop("positions", [])
        tabs["type"] = "plugin"
        tabs["code"] = "kikit_viewer.plugins.manual_tabs.ManualTabsPlugin"
        tabs["arg"]  = _json.dumps(positions)

    return config


def _format_error(exc: Exception) -> str:
    import tempfile, os
    full = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log_path = os.path.join(tempfile.gettempdir(), "kikit_viewer_error.log")
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(full)
    except OSError:
        pass
    lines = full.strip().splitlines()
    summary = "\n".join(lines[-30:])
    return f"{summary}\n\n(Full traceback written to {log_path})"
