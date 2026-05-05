from __future__ import annotations

import sys
import traceback
from pathlib import Path

_LOG_PATH = Path(__file__).parent.parent.parent / "kikit_viewer_crash.log"


def _write_log(text: str) -> None:
    try:
        _LOG_PATH.write_text(text, encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    try:
        _run()
    except Exception:
        _write_log(traceback.format_exc())
        raise


def _run() -> None:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("KiKit Viewer")
    app.setOrganizationName("KiKitViewer")

    # MainWindow (and any icon libraries it imports) must be imported after
    # QApplication is created — qtawesome and similar libraries register fonts
    # with Qt's font database at import time, which requires a live QApplication.
    from kikit_viewer.ui.main_window import MainWindow

    _icon = Path(__file__).parent / "resources" / "app_icon.ico"
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))

    # board_path may be passed as argv[1] by the pcbnew plugin
    board_path: Path | None = None
    if len(sys.argv) >= 2:
        candidate = Path(sys.argv[1])
        if candidate.exists():
            board_path = candidate

    window = MainWindow(board_path=board_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
