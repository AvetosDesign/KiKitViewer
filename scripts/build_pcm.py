"""
Build a KiCad PCM-compatible zip for KiKit Viewer.

Usage:
    python scripts/build_pcm.py [--update-meta]

Options:
    --update-meta   Patch metadata.json in-place with the computed sha256,
                    download_size, and install_size for the latest version entry.

Output:
    dist/kikit-viewer-{version}.zip
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
META_PATH = ROOT / "metadata.json"
SRC_PKG = ROOT / "src" / "kikit_viewer"
PLUGINS_DIR = ROOT / "plugins"
ICON_SRC = ROOT / "KiKitViewerIcon.png"
DIST_DIR = ROOT / "dist"

_EXCLUDE_NAMES = {"__pycache__", ".venv", ".vscode", "tests", "Thumbs.db"}
_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".pyd"}


def _read_meta() -> dict:
    return json.loads(META_PATH.read_text(encoding="utf-8"))


def _copy_tree(src: Path, dst: Path) -> int:
    """Copy src directory tree to dst, skipping excluded files. Returns total bytes."""
    total = 0
    for item in src.rglob("*"):
        if any(p in _EXCLUDE_NAMES for p in item.parts):
            continue
        if item.suffix in _EXCLUDE_SUFFIXES:
            continue
        if item.is_file():
            rel = item.relative_to(src)
            target = dst / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            total += item.stat().st_size
    return total


def _resize_icon(src: Path, dst: Path) -> None:
    """Copy icon to dst, resizing to 64x64 if Pillow is available."""
    try:
        from PIL import Image  # type: ignore[import]
        img = Image.open(src).convert("RGBA")
        img = img.resize((64, 64), Image.LANCZOS)
        img.save(dst)
    except ImportError:
        shutil.copy2(src, dst)


def build(update_meta: bool = False) -> None:
    meta = _read_meta()
    version = meta["versions"][0]["version"]
    zip_name = f"kikit-viewer-{version}.zip"

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = DIST_DIR / zip_name

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        # metadata.json
        shutil.copy2(META_PATH, tmp / "metadata.json")

        # plugins/ — the KiCad ActionPlugin entry point
        plugins_dst = tmp / "plugins"
        plugins_dst.mkdir()
        for f in PLUGINS_DIR.iterdir():
            if f.name in _EXCLUDE_NAMES or f.suffix in _EXCLUDE_SUFFIXES:
                continue
            if f.is_file():
                shutil.copy2(f, plugins_dst / f.name)

        # plugins/kikit_viewer/ — the bundled Python package
        pkg_dst = plugins_dst / "kikit_viewer"
        pkg_dst.mkdir()
        install_size = _copy_tree(SRC_PKG, pkg_dst)

        # Add the plugins/ files themselves to install size
        for f in plugins_dst.iterdir():
            if f.is_file():
                install_size += f.stat().st_size

        # resources/icon.png — 64x64 icon for the PCM browser
        resources_dst = tmp / "resources"
        resources_dst.mkdir()
        if ICON_SRC.exists():
            _resize_icon(ICON_SRC, resources_dst / "icon.png")
        else:
            # Fall back to the toolbar icon (24x24)
            fallback = PLUGINS_DIR / "icon.png"
            if fallback.exists():
                shutil.copy2(fallback, resources_dst / "icon.png")

        # Zip everything
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(tmp.rglob("*")):
                if item.is_file():
                    zf.write(item, item.relative_to(tmp))

    # Stats
    download_size = zip_path.stat().st_size
    sha256 = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    print(f"Output:        {zip_path}")
    print(f"download_size: {download_size}")
    print(f"install_size:  {install_size}")
    print(f"sha256:        {sha256}")

    if update_meta:
        meta["versions"][0]["download_sha256"] = sha256
        meta["versions"][0]["download_size"] = download_size
        meta["versions"][0]["install_size"] = install_size
        META_PATH.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("metadata.json updated.")
    else:
        print("\nPaste into metadata.json versions[0]:")
        print(f'  "download_sha256": "{sha256}",')
        print(f'  "download_size": {download_size},')
        print(f'  "install_size": {install_size}')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update-meta", action="store_true",
                        help="Patch metadata.json in-place with computed hash and sizes")
    args = parser.parse_args()
    build(update_meta=args.update_meta)
