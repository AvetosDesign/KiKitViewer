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
        import shutil
        kikit_config = _preprocess_config(to_kikit(self._config))

        # Copy the original board to the temp directory before passing it to
        # the worker.  pcbnew.LoadBoard() can create/touch auxiliary files
        # (.kicad_pro, .kicad_prl) next to the board it opens; doing that
        # inside the temp dir keeps the original project directory clean and
        # prevents KiCad from seeing those side-effect writes and marking the
        # open board as modified.
        tmp_board = self._output_path.parent / self._board_path.name
        shutil.copy2(self._board_path, tmp_board)

        worker = Path(__file__).parent / "panel_worker.py"
        payload = json.dumps({
            "board_path":  str(tmp_board),
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

    text = config.get("text", {})
    if text.get("type") == "scripted":
        script_path = text.pop("script", "").strip()
        if script_path:
            text["text"] = _run_text_script(script_path)
        else:
            text["text"] = "<no script specified>"
        text["type"] = "simple"
    else:
        text.pop("script", None)

    return config


def _run_text_script(script_path: str) -> str:
    """
    Execute a Python script that defines get_text() -> str and return its result.

    The script runs in an isolated namespace. It must define a top-level
    function named get_text() that returns a plain string.
    """
    path = Path(script_path)
    if not path.is_file():
        raise FileNotFoundError(f"Script not found: {script_path}")
    ns: dict = {}
    exec(path.read_text(encoding="utf-8"), ns)  # noqa: S102
    get_text = ns.get("get_text")
    if not callable(get_text):
        raise AttributeError(
            f"Script '{script_path}' must define a get_text() function"
        )
    result = get_text()
    if not isinstance(result, str):
        raise TypeError(
            f"get_text() must return str, got {type(result).__name__}"
        )
    return result


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
