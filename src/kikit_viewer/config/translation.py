from __future__ import annotations

from typing import Any

# Top-level section name reserved for KiKitViewer-specific metadata.
# KiKit never sees this key — it is stripped before the config reaches the runner.
_VIEWER_SECTION = "kikit_viewer"


def to_kikit(config: dict[str, Any]) -> dict[str, Any]:
    """
    Return a KiKit-compatible copy of a .kicad_panel config dict.

    Strips any KiKitViewer-specific sections so the result can be passed
    directly to the KiKit API. This is the sole translation point between
    KiKitViewer's file format and KiKit's native format — extend here if
    deeper translation is ever needed.
    """
    return {k: v for k, v in config.items() if k != _VIEWER_SECTION}


def viewer_meta(config: dict[str, Any]) -> dict[str, Any]:
    """Extract the kikit_viewer metadata section (empty dict if absent)."""
    return dict(config.get(_VIEWER_SECTION, {}))


def with_viewer_meta(config: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of config with the kikit_viewer section set to meta."""
    result = dict(config)
    result[_VIEWER_SECTION] = meta
    return result
