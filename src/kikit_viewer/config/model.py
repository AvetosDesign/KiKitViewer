from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from kikit_viewer.config import schema


class ConfigModel(QObject):
    """
    Live KiKit configuration state.

    Wraps a dict that matches KiKit's JSON config schema. All parameter panel
    widgets read from and write to this model. Any change emits config_changed,
    which the RunCoordinator uses to schedule a new KiKit run.

    The board_path is stored separately — it is passed to KiKit as the source
    board, not embedded in the config dict itself.
    """

    config_changed = Signal()
    board_path_changed = Signal(Path)
    undo_state_changed = Signal(bool, bool)  # can_undo, can_redo

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._board_path: Path | None = None
        self._config: dict[str, dict[str, Any]] = schema.defaults()
        self._undo_stack: list[dict] = []
        self._redo_stack: list[dict] = []
        self._undo_pending: bool = False

    # ------------------------------------------------------------------
    # Board path
    # ------------------------------------------------------------------

    @property
    def board_path(self) -> Path | None:
        return self._board_path

    @board_path.setter
    def board_path(self, path: Path | str | None) -> None:
        if path is not None:
            path = Path(path)
        if path != self._board_path:
            self._board_path = path
            self.board_path_changed.emit(path)

    # ------------------------------------------------------------------
    # Config access
    # ------------------------------------------------------------------

    def get(self, section: str, key: str) -> Any:
        return self._config[section][key]

    def set(self, section: str, key: str, value: Any) -> None:
        if not self._undo_pending:
            self._undo_stack.append(copy.deepcopy(self._config))
            if len(self._undo_stack) > 50:
                self._undo_stack.pop(0)
            self._redo_stack.clear()
            self._undo_pending = True
            QTimer.singleShot(0, self._clear_undo_pending)
            self.undo_state_changed.emit(True, False)
        if self._config[section].get(key) != value:
            self._config[section][key] = value
            self.config_changed.emit()

    def _clear_undo_pending(self) -> None:
        self._undo_pending = False

    def undo(self) -> None:
        if not self._undo_stack:
            return
        self._redo_stack.append(copy.deepcopy(self._config))
        self._config = self._undo_stack.pop()
        self.config_changed.emit()
        self.undo_state_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    def redo(self) -> None:
        if not self._redo_stack:
            return
        self._undo_stack.append(copy.deepcopy(self._config))
        self._config = self._redo_stack.pop()
        self.config_changed.emit()
        self.undo_state_changed.emit(bool(self._undo_stack), bool(self._redo_stack))

    def section(self, name: str) -> dict[str, Any]:
        """Return a shallow copy of one config section."""
        return dict(self._config[name])

    def as_dict(self) -> dict[str, dict[str, Any]]:
        """Return a deep copy of the full config dict."""
        return {s: dict(v) for s, v in self._config.items()}

    def load_dict(self, data: dict[str, dict[str, Any]]) -> None:
        """Replace config from a dict (e.g., loaded from a .kikit.json file)."""
        defaults = schema.defaults()
        for section, fields in data.items():
            if section not in defaults:
                continue
            known_keys = set(defaults[section].keys())
            for k, v in fields.items():
                if k in known_keys:
                    defaults[section][k] = v
        self._config = defaults
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._undo_pending = False
        self.undo_state_changed.emit(False, False)
        self.config_changed.emit()

    def reset_to_defaults(self) -> None:
        """Reset all parameters to KiKit defaults (board path is preserved)."""
        self._config = schema.defaults()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._undo_pending = False
        self.undo_state_changed.emit(False, False)
        self.config_changed.emit()

    # ------------------------------------------------------------------
    # Derived paths
    # ------------------------------------------------------------------

    def default_config_path(self) -> Path | None:
        """<boardname>.kicad_panel alongside the source PCB."""
        if self._board_path is None:
            return None
        return self._board_path.with_suffix(".kicad_panel")

    def default_panel_path(self) -> Path | None:
        """<boardname>-panel.kicad_pcb alongside the source PCB."""
        if self._board_path is None:
            return None
        stem = self._board_path.stem
        return self._board_path.with_name(f"{stem}-panel.kicad_pcb")
