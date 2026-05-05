from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import pcbnew  # type: ignore[import]
except ImportError:
    pcbnew = None  # allows importing this file outside KiCad for testing


_PLUGIN_DIR = Path(__file__).parent
_ICON_PATH = _PLUGIN_DIR / "icon.png"
# PCM layout: kikit_viewer/ is bundled inside plugins/
# Dev layout:  kikit_viewer/ lives in ../src/
_bundled = _PLUGIN_DIR / "kikit_viewer"
_SRC_DIR = _PLUGIN_DIR if _bundled.exists() else _PLUGIN_DIR.parent / "src"
_MAIN_SCRIPT = _SRC_DIR / "kikit_viewer" / "main.py"

# KiCad's pcbnew and scripting modules live here — must be on PYTHONPATH
# so that kikit (which imports pcbnew at load time) can find it.
_KICAD_SITE_PACKAGES = Path(sys.executable).parent / "Lib" / "site-packages"
_REQUIRED_PACKAGES = ["PySide6", "kikit", "shapely", "qtawesome"]


def _find_python() -> str:
    """
    Locate a Python interpreter that is not KiCad's embedded one.

    Priority:
      1. Project .venv (sibling of src\)
      2. 'python' on the system PATH that is not inside KiCad's bin
      3. 'python3' on the system PATH
    """
    # 1. Project venv
    venv_python = _PLUGIN_DIR.parent / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)

    # 2 & 3. System PATH — skip anything inside the KiCad installation
    kicad_bin = Path(sys.executable).parent
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found and Path(found).parent != kicad_bin:
            return found

    # Last resort: whatever python.exe lives next to KiCad's own executable
    kicad_python = kicad_bin / "python.exe"
    if kicad_python.exists():
        return str(kicad_python)

    return "python"


def _check_dependencies(python_exe: str) -> list[str]:
    """Return list of package names that cannot be imported by python_exe."""
    probe = "; ".join(f"import {p}" for p in _REQUIRED_PACKAGES)
    try:
        result = subprocess.run(
            [python_exe, "-c", probe],
            capture_output=True, timeout=15,
        )
        if result.returncode == 0:
            return []
        missing = []
        for pkg in _REQUIRED_PACKAGES:
            r = subprocess.run(
                [python_exe, "-c", f"import {pkg}"],
                capture_output=True, timeout=10,
            )
            if r.returncode != 0:
                missing.append(pkg)
        return missing
    except Exception:
        return []  # can't probe — let the launch attempt proceed normally


def _show_missing_deps_error(missing: list[str]) -> None:
    pkgs = " ".join(missing)
    msg = (
        "KiKit Viewer: missing Python dependencies.\n\n"
        "The following packages are not available in the selected Python interpreter:\n"
        f"  {', '.join(missing)}\n\n"
        f"Install them with:\n"
        f"  pip install {pkgs}\n\n"
        "Then restart KiCad."
    )
    try:
        import wx  # type: ignore[import]
        wx.MessageBox(msg, "KiKit Viewer", wx.OK | wx.ICON_ERROR)
    except Exception:
        print(msg, file=sys.stderr)


class KiKitViewerPlugin(pcbnew.ActionPlugin if pcbnew else object):
    """
    pcbnew ActionPlugin — adds a KiKit Viewer button to the pcbnew toolbar.

    When clicked, reads the current board filename from pcbnew and launches
    the KiKitViewer GUI as a separate subprocess. Running out-of-process avoids
    any wx/Qt event-loop conflicts.
    """

    def defaults(self) -> None:
        self.name = "KiKit Viewer"
        self.category = "Panelization"
        self.description = "Open the KiKit visual panel editor for the current board"
        self.show_toolbar_button = True
        self.icon_file_name = str(_ICON_PATH) if _ICON_PATH.exists() else ""

    def Run(self) -> None:  # noqa: N802 — KiCad requires this capitalisation
        if pcbnew is None:
            return

        board = pcbnew.GetBoard()
        board_path = board.GetFileName() if board else ""

        python_exe = _find_python()

        missing = _check_dependencies(python_exe)
        if missing:
            _show_missing_deps_error(missing)
            return

        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        extra = os.pathsep.join([str(_SRC_DIR), str(_KICAD_SITE_PACKAGES)])
        env["PYTHONPATH"] = f"{extra}{os.pathsep}{existing}" if existing else extra

        subprocess.Popen(
            [python_exe, str(_MAIN_SCRIPT), board_path],
            env=env,
            # Detach so closing pcbnew doesn't kill the viewer
            creationflags=subprocess.DETACHED_PROCESS if sys.platform == "win32" else 0,
        )
