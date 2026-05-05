from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, dict[str, Any]]:
    """Load a .kicad_panel or .kikit.json file and return the raw config dict."""
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} is not a valid panel config file")
    return data


def save(config: dict[str, dict[str, Any]], path: Path) -> None:
    """Write a config dict to a .kicad_panel (or .kikit.json) file."""
    path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
