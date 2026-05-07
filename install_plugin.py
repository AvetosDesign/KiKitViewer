#!/usr/bin/env python3
"""
Install the KiKitViewer plugin stub into KiCad's user scripting directory.

A small stub __init__.py is written into the KiCad plugin folder. The stub
adds the actual source tree to sys.path at load time, so all edits to
plugins/ in this repo are live immediately without reinstalling.

Usage:
    python install_plugin.py           # auto-detect newest KiCad version
    python install_plugin.py --list    # list all detected KiCad versions
    python install_plugin.py 9.0       # target a specific version
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


PLUGIN_NAME = "kikit_viewer"
SCRIPT_DIR = Path(__file__).resolve().parent
SOURCE_DIR = SCRIPT_DIR / "plugins"

STUB_TEMPLATE = """\
import sys as _sys
_src = {source!r}
if _src not in _sys.path:
    _sys.path.insert(0, _src)
from kikit_viewer_plugin import KiKitViewerPlugin
KiKitViewerPlugin().register()
"""


def _kicad_roots() -> list[Path]:
    """Return candidate parent directories that contain per-version KiCad subdirs."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        return [
            Path(appdata) / "kicad",
            Path.home() / "Documents" / "KiCad",
        ]
    if sys.platform == "darwin":
        return [
            Path.home() / "Library" / "Preferences" / "kicad",
        ]
    # Linux / other Unix
    xdg = os.environ.get("XDG_DATA_HOME", "")
    return [
        Path(xdg) / "kicad" if xdg else Path.home() / ".local" / "share" / "kicad",
        Path.home() / ".config" / "kicad",
    ]


def _find_plugin_dirs() -> list[tuple[str, Path]]:
    """
    Return [(version_str, scripting/plugins path), ...] for all detected
    KiCad versions, sorted newest-first.
    """
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for root in _kicad_roots():
        if not root.is_dir():
            continue
        for child in root.iterdir():
            plugins_dir = child / "scripting" / "plugins"
            if not plugins_dir.exists():
                continue
            resolved = plugins_dir.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append((child.name, plugins_dir))

    def _version_key(item: tuple[str, Path]) -> tuple[int, ...]:
        parts = []
        for part in item[0].split("."):
            try:
                parts.append(int(part))
            except ValueError:
                parts.append(0)
        return tuple(parts)

    found.sort(key=_version_key, reverse=True)
    return found


def _write_stub(plugins_dir: Path) -> None:
    plugin_dir = plugins_dir / PLUGIN_NAME
    plugin_dir.mkdir(parents=True, exist_ok=True)
    stub_path = plugin_dir / "__init__.py"
    stub_path.write_text(
        STUB_TEMPLATE.format(source=str(SOURCE_DIR)),
        encoding="utf-8",
    )
    print(f"  Wrote stub → {stub_path}")
    print(f"  Stub points to: {SOURCE_DIR}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", help="KiCad version to target (e.g. 9.0)")
    parser.add_argument("--list", action="store_true", help="List detected KiCad versions and exit")
    args = parser.parse_args()

    versions = _find_plugin_dirs()

    if args.list:
        if not versions:
            print("No KiCad scripting plugin directories found.")
        else:
            print("Detected KiCad scripting plugin directories:")
            for ver, path in versions:
                print(f"  {ver:10s}  {path}")
        return

    if args.version:
        match = [(v, p) for v, p in versions if v == args.version]
        if not match:
            # Version not found yet — construct the path from the first root that exists
            for root in _kicad_roots():
                candidate = root / args.version / "scripting" / "plugins"
                if root.exists():
                    match = [(args.version, candidate)]
                    break
        if not match:
            print(f"Error: could not locate a KiCad plugins directory for version {args.version!r}.")
            print("Run with --list to see what was detected, or create the directory manually.")
            sys.exit(1)
        target_ver, target_dir = match[0]
    elif versions:
        target_ver, target_dir = versions[0]
        print(f"Auto-selected KiCad {target_ver} (newest detected).")
        if len(versions) > 1:
            others = ", ".join(v for v, _ in versions[1:])
            print(f"Other versions found: {others}  (pass a version argument to target a different one)")
    else:
        print("No KiCad scripting plugin directories found.")
        print()
        print("KiCad stores plugins in:")
        for root in _kicad_roots():
            print(f"  {root / '<version>' / 'scripting' / 'plugins'}")
        print()
        print("Either install KiCad first, or pass the target version explicitly:")
        print(f"  python {Path(__file__).name} 9.0")
        sys.exit(1)

    print()
    print(f"Installing KiKit Viewer plugin for KiCad {target_ver}:")
    _write_stub(target_dir)
    print()
    print("Restart KiCad to load the plugin and see the toolbar button.")


if __name__ == "__main__":
    main()
