# KiKit Viewer

A visual panel editor for [KiKit](https://github.com/yaqwsx/KiKit) that runs as a KiCad pcbnew plugin. Design and preview PCB panels interactively without editing JSON by hand.

![KiKit Viewer](KiKitViewerIcon.png)

## Features

- **Live preview** — panel re-renders automatically as you adjust parameters (or manually via F5)
- **Layout** — grid layout with rows, columns, spacing, rotation, and alternation; manual placement mode with drag-and-drop positioning
- **Tabs** — fixed, spacing, corner, full, annotation, and manual tab placement; manual mode lets you drag tab markers onto board edges
- **Framing** — frame, tight frame, rails (top/bottom or left/right); fiducials and tooling holes with draggable handles
- **Corner treatment** — chamfer and fillet with per-axis control
- **Layer visibility** — per-layer toggle with color swatches matching the active KiCad color theme
- **Undo/Redo** — full undo stack (Ctrl+Z / Ctrl+Y)
- **Save/Load** — `.kicad_panel` format (JSON); legacy `.kikit.json` files load cleanly
- **Export** — copies the finished panel `.kicad_pcb` to a location of your choice

## Requirements

- **KiCad 8.0** or later
- **Python 3.11+** (separate from KiCad's embedded Python)
- The following Python packages installed in your external Python environment:

```
pip install kikit>=1.4 PySide6>=6.6 shapely>=2.0 qtawesome>=1.3
```

## Installation

### Via KiCad Plugin Content Manager (recommended)

> PCM submission pending. In the meantime, use the manual method below.

### Manual installation

1. Clone or download this repository.
2. Run `install_plugin.bat` (Windows). This writes a stub into KiCad's scripting/plugins folder that points back to the cloned source — edits are live immediately.
3. Restart KiCad. A **KiKit Viewer** button will appear in the pcbnew toolbar.

## Usage

1. Open a board in pcbnew.
2. Click the **KiKit Viewer** toolbar button. The viewer opens in a separate window with the current board pre-loaded.
3. Adjust panelization parameters in the left dock (Layout, Tabs, Framing, Cuts, Post).
4. The panel preview updates automatically. Use **F5** or the Refresh button to trigger a manual update.
5. **Save** your configuration as a `.kicad_panel` file (File → Save, Ctrl+S).
6. **Export** the finished panel board (File → Export).

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| F5 | Refresh panel |
| Ctrl+0 / Home | Fit panel in view |
| Ctrl++ / Ctrl+- | Zoom in / out |
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Ctrl+S | Save |
| Delete | Remove selected tab marker (manual tabs mode) |

### Manual tab placement

Switch the **Tabs** panel to type **manual**, then:
- **Left-click** on the canvas to place a tab on the nearest board edge
- **Right-click** on the canvas → **Add Tab Here**
- Drag tab markers to reposition them (they snap to the board outline on release)
- Right-click a marker → **Remove**, or select it and press **Delete**

### Manual board placement

Switch the **Layout** panel to type **manual**, then:
- Click a row in the position table to select a board — a white outline appears on the canvas
- Drag the outline to reposition the board
- Click a board outline on the canvas to select its table row

## File format

KiKit Viewer saves configurations as `.kicad_panel` files — standard JSON with an extra `kikit_viewer` section for UI state (layer visibility, etc.). The KiKit section is identical to a `.kikit.json` preset file and can be used directly with the KiKit CLI:

```
kikit panelize --preset my_panel.kicad_panel board.kicad_pcb panel.kicad_pcb
```

## Building the PCM package

```
python scripts/build_pcm.py
```

Produces `dist/kikit-viewer-{version}.zip` ready for KiCad's Plugin Content Manager. Run with `--update-meta` to stamp the SHA256 and sizes into `metadata.json` automatically.

## License

MIT — see [LICENSE](LICENSE).
