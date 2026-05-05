from __future__ import annotations

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

    finished = Signal(Path)
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
            self._invoke_kikit()
            self.finished.emit(self._output_path)
        except Exception as exc:
            self.failed.emit(_format_error(exc))

    def _invoke_kikit(self) -> None:
        # Force-reload our own plugins so edits take effect within a session.
        # KiKit's SPlugin uses importlib.import_module which caches modules in
        # sys.modules; without this, the first-imported version is reused forever.
        import sys
        for key in [k for k in sys.modules if k.startswith("kikit_viewer.plugins")]:
            del sys.modules[key]

        try:
            from kikit import panelize_ui             # type: ignore[import]
            from kikit import panelize_ui_impl as ki  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "KiKit is not installed or not importable. "
                "Install it with: pip install kikit"
            ) from exc

        from kikit_viewer.config.translation import to_kikit
        kikit_config = _preprocess_config(to_kikit(self._config))

        type_map = _build_type_map()
        schema_keys = _build_schema_keys()

        # Convert our config values to the string format KiKit validators expect.
        # Only pass keys that are in our schema — prevents shorthand fields like
        # "space" (which overrides hspace/vspace in KiKit's ppLayout) from
        # leaking in via old saved configs or load_dict.
        section_overrides = {
            section: _format_section(
                {k: v for k, v in values.items() if k in schema_keys.get(section, set())},
                type_map.get(section, {}),
            )
            for section, values in kikit_config.items()
        }

        # code/arg are not in our schema (to keep the config clean) but KiKit
        # needs them when type="plugin".  Inject them directly after filtering.
        if kikit_config.get("layout", {}).get("type") == "plugin":
            section_overrides.setdefault("layout", {})
            section_overrides["layout"]["code"] = str(kikit_config["layout"].get("code", ""))
            section_overrides["layout"]["arg"]  = str(kikit_config["layout"].get("arg", ""))

        if kikit_config.get("tabs", {}).get("type") == "plugin":
            section_overrides.setdefault("tabs", {})
            section_overrides["tabs"]["code"] = str(kikit_config["tabs"].get("code", ""))
            section_overrides["tabs"]["arg"]  = str(kikit_config["tabs"].get("arg", ""))

        # obtainPreset starts from KiKit's ":default", merges our overrides,
        # validates all values, and post-processes them into typed objects.
        preset = ki.obtainPreset([], **section_overrides)

        panelize_ui.doPanelization(
            str(self._board_path),
            str(self._output_path),
            preset,
        )


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


def _build_schema_keys() -> dict[str, set[str]]:
    """Return {section: {field_key, ...}} from our own schema — the authoritative allowlist."""
    from kikit_viewer.config.schema import SECTIONS
    return {section: {f.key for f in fields} for section, fields in SECTIONS.items()}


def _build_type_map() -> dict[str, dict[str, str]]:
    """
    Build {section: {field: type_name}} from KiKit's own section definitions.

    availableSections keys are capitalized ('Layout') but preset keys are
    lowercase ('layout') — we normalise to lowercase here.
    """
    try:
        from kikit import panelize_ui  # type: ignore[import]
    except ImportError:
        return {}

    result: dict[str, dict[str, str]] = {}
    for section_name, fields in panelize_ui.availableSections.items():
        key = section_name.lower()
        result[key] = {fname: type(fval).__name__ for fname, fval in fields.items()}
    return result


def _format_section(values: dict[str, Any], types: dict[str, str]) -> dict[str, Any]:
    """
    Format a config section's values into strings that KiKit validators accept.

    Only fields that appear in KiKit's section definition (i.e. present in
    `types`) are included — unknown keys are silently dropped so schema
    mismatches never cause preset errors.

      SLength        → "3.0mm"
      SAngle         → "90.0deg"
      SBool          → "True" / "False"
      everything else → str(value)
    """
    result = {}
    for k, v in values.items():
        field_type = types.get(k)
        if field_type is None:
            continue  # not a real KiKit field — skip
        if field_type == "SLength":
            result[k] = f"{v}mm" if isinstance(v, (int, float)) else str(v)
        elif field_type == "SAngle":
            result[k] = f"{v}deg" if isinstance(v, (int, float)) else str(v)
        elif isinstance(v, bool):
            result[k] = str(v)
        else:
            result[k] = str(v)
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
