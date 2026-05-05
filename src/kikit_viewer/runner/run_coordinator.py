from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from kikit_viewer.config.model import ConfigModel
from kikit_viewer.runner.kikit_runner import KiKitRunner

# Milliseconds to wait after the last config change before firing a run.
_DEBOUNCE_MS = 600


class RunCoordinator(QObject):
    """
    Manages the KiKit run lifecycle: debouncing, thread ownership, and re-queuing.

    When config_changed fires, a 600ms debounce timer is (re)started. On expiry,
    a KiKitRunner thread is launched. If another change arrives while a run is in
    progress, one additional run is queued for after completion — excess changes
    are dropped.

    Signals
    -------
    run_started :
        Emitted when a KiKit run begins.
    run_finished : Path
        Emitted with the output PCB path on success.
    run_failed : str
        Emitted with an error message on failure.
    """

    run_started = Signal()
    run_finished = Signal(Path)
    run_failed = Signal(str)

    def __init__(self, model: ConfigModel, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._runner: KiKitRunner | None = None
        self._pending = False          # re-run requested while one is active
        self._auto_refresh = True
        self._tmp_dir = tempfile.mkdtemp(prefix="kikit_viewer_")

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._fire)

        model.config_changed.connect(self.schedule)
        model.board_path_changed.connect(self._on_board_path_changed)

    @property
    def auto_refresh(self) -> bool:
        return self._auto_refresh

    @auto_refresh.setter
    def auto_refresh(self, value: bool) -> None:
        self._auto_refresh = value

    def schedule(self) -> None:
        """Restart the debounce timer. No-op when auto_refresh is disabled."""
        if self._model.board_path is None or not self._auto_refresh:
            return
        self._timer.start()

    def run_now(self) -> None:
        """Immediately fire a run, bypassing the auto_refresh gate."""
        self._timer.stop()
        self._fire()

    def _on_board_path_changed(self, path: Path | None) -> None:
        if path is not None and self._auto_refresh:
            self._timer.start()

    def _fire(self) -> None:
        if self._model.board_path is None:
            return
        if self._runner is not None and self._runner.isRunning():
            self._pending = True
            return
        self._launch()

    def _launch(self) -> None:
        board_path = self._model.board_path
        if board_path is None:
            return

        output_path = Path(self._tmp_dir) / "panel_preview.kicad_pcb"
        config = self._model.as_dict()

        self._runner = KiKitRunner(board_path, config, output_path, parent=self)
        self._runner.finished.connect(self._on_finished)
        self._runner.failed.connect(self._on_failed)
        self._runner.start()
        self.run_started.emit()

    def _on_finished(self, path: Path) -> None:
        self.run_finished.emit(path)
        self._check_pending()

    def _on_failed(self, message: str) -> None:
        self.run_failed.emit(message)
        self._check_pending()

    def _check_pending(self) -> None:
        if self._pending:
            self._pending = False
            self._launch()
