# KiKitViewer — Implementation Plan

## Vision

A visual GUI editor for KiKit PCB panelization, delivered as a **pcbnew Action
Plugin**. KiKit handles all panelization logic; the GUI is a live-preview
parameter editor with interactive canvas handles.

**Workflow:**
1. User finishes PCB layout in pcbnew
2. Clicks the KiKitViewer toolbar button (registered ActionPlugin)
3. Plugin reads the current board path from `pcbnew.GetBoard().GetFileName()` —
   no file picker, no copy-paste
4. KiKitViewer GUI launches as a separate process, receiving the board path
5. User adjusts KiKit parameters via tabbed panels and/or canvas handles
6. App runs KiKit (Python API) in background thread → outputs temp `.kicad_pcb`
7. Output PCB rendered as per-layer SVGs → displayed on canvas
8. Canvas overlays draggable handles for tabs and fiducials
9. User saves the KiKit JSON config and/or exports the panel `.kicad_pcb`

---

## Key Design Decisions

### KiKit is the engine
We never reimplement panelization. KiKit's Python API is the sole source of panel
geometry. If KiKit can't do it, we don't do it.

### KiKit JSON config is the document format
KiKit already defines a JSON preset/config schema. We save and load that directly.
No custom `.kicad_panel` format — the file the user saves is a valid KiKit config
file they can also use from the CLI.

### Debounced background runs
KiKit runs take 1–5 seconds. Every parameter change arms a 600ms debounce timer.
When it fires, KiKit runs in a QThread. The canvas shows a subtle "updating..."
indicator. Rapid edits don't queue multiple runs.

### Canvas: rendered SVG + interactive overlay
The canvas displays the output PCB as composited SVG layers (reused from
PanelEditor's `pcbnew_renderer.py`). Qt graphics items are overlaid on top for
interactive handles — they don't modify the SVG, they modify the config.

### Tab handle interaction → fixed tab mode
KiKit supports `type: fixed` tabs where positions are explicit coordinates.
When a user drags a tab handle, that board edge's tab spec is promoted to
`type: fixed` with the new position written back to config. Dragging away from
a board edge snaps to nearest valid position.

### Fiducial handle interaction
Fiducials have a position spec. Dragging a fiducial handle updates its offset
in the config.

### pcbnew plugin — wx/Qt process isolation
pcbnew runs a wxPython event loop. Mixing a PySide6 event loop in the same
process is unreliable on Windows. The `ActionPlugin.Run()` method does one
thing only: launch `kikit_viewer` as a subprocess with the board path as an
argument. The GUI runs in its own process with its own Qt event loop — no
conflicts.

```python
# plugin/kikit_viewer_plugin.py
class KiKitViewerPlugin(pcbnew.ActionPlugin):
    def Run(self):
        board_path = pcbnew.GetBoard().GetFileName()
        subprocess.Popen([sys.executable, "-m", "kikit_viewer", board_path])
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      MainWindow                           │
│  ┌────────────────────────┐  ┌───────────────────────┐   │
│  │  PanelCanvas           │  │  ParameterDock        │   │
│  │  QGraphicsView/Scene   │  │  QTabWidget           │   │
│  │  ├─ SVG layer items    │  │  ├─ Source tab        │   │
│  │  ├─ TabHandleItems     │  │  ├─ Layout tab        │   │
│  │  ├─ FiducialHandleItems│  │  ├─ Tabs tab          │   │
│  │  └─ ContextMenus       │  │  ├─ Cuts tab          │   │
│  └────────────┬───────────┘  │  ├─ Framing tab       │   │
│               │signals        │  ├─ Tooling tab       │   │
│  ┌────────────▼───────────┐  │  ├─ Fiducials tab     │   │
│  │  RunCoordinator        │  │  ├─ Text tab           │   │
│  │  (debounce + QThread)  │  │  └─ Post tab          │   │
│  └────────────┬───────────┘  └───────────┬───────────┘   │
└───────────────│───────────────────────────│───────────────┘
                │                           │
                ▼                           ▼
     ┌──────────────────┐        ┌──────────────────┐
     │  KiKitRunner     │        │  ConfigModel     │
     │  (QThread)       │        │  (dict + helpers)│
     │  kikit Python API│        │  save/load JSON  │
     └──────────┬───────┘        └──────────────────┘
                │
                ▼
     ┌──────────────────┐
     │  PcbnewRenderer  │        ← reused from PanelEditor
     │  SVG per layer   │
     └──────────────────┘
```

---

## Module Layout

```
plugin/                        # KiCad PCM plugin package root
├── metadata.json              # PCM package descriptor (identifier, version, etc.)
├── plugins/
│   └── kikit_viewer_plugin.py # ActionPlugin subclass — launches subprocess
└── resources/
    └── icon.png               # 24×24 toolbar icon shown in pcbnew

src/kikit_viewer/
├── main.py                    # QApplication entry point; accepts board_path argv[1]
├── __init__.py
│
├── config/
│   ├── model.py               # ConfigModel: dict wrapper around KiKit JSON schema
│   ├── schema.py              # KiKit parameter schema (sections, fields, types, defaults)
│   ├── serialization.py       # load/save KiKit JSON config files
│   └── __init__.py
│
├── runner/
│   ├── kikit_runner.py        # KiKitRunner QThread: config → temp PCB via kikit API
│   ├── run_coordinator.py     # RunCoordinator: debounce timer + runner lifecycle
│   └── __init__.py
│
├── renderer/
│   ├── pcbnew_renderer.py     # (copied/adapted from PanelEditor) SVG layer rendering
│   └── __init__.py
│
└── ui/
    ├── main_window.py         # MainWindow: layout, menus, toolbar, wires everything
    ├── __init__.py
    ├── canvas/
    │   ├── view.py            # PanelView: QGraphicsView, pan/zoom
    │   ├── scene.py           # PanelScene: QGraphicsScene, item management
    │   ├── board_layer_item.py # SVG layer rendering item
    │   ├── tab_handle_item.py # Draggable tab handle overlaid on canvas
    │   ├── fiducial_handle_item.py  # Draggable fiducial handle
    │   └── __init__.py
    └── params/
        ├── source_panel.py    # Source PCB file picker, reference offset
        ├── layout_panel.py    # Grid rows/cols, spacing, rotation
        ├── tabs_panel.py      # Tab type, width, count/spacing
        ├── cuts_panel.py      # Cut type (mousebites/vcuts), drill, spacing
        ├── framing_panel.py   # Frame type, width, cuts
        ├── tooling_panel.py   # Tooling holes type, diameter, spacing
        ├── fiducials_panel.py # Fiducial type, size, corner offsets
        ├── text_panel.py      # Panel text options
        ├── post_panel.py      # Post-processing (millFillet, copperfill, etc.)
        └── __init__.py
```

---

## ConfigModel

Wraps a KiKit-compatible config dict. KiKit sections:

| Section      | Key parameters |
|-------------|----------------|
| `layout`    | type, rows, cols, hspace, vspace, rotation, alternation |
| `source`    | type, tolerance, tlx/tly/brx/bry (explicit board area) |
| `tabs`      | type, width, vcount/hcount, spacing, minDistance |
| `cuts`      | type, drill, spacing, offset, prolong |
| `framing`   | type, width, hspace, vspace, cuts, chamfer/fillet |
| `tooling`   | type, hoffset, voffset, diameter, paste |
| `fiducials` | type, hoffset, voffset, copperSize, opening, paste |
| `text`      | (various annotation options) |
| `post`      | millFillet, copperfill, cutoutMargin |
| `page`      | type, anchor |

`ConfigModel` emits a Qt signal `config_changed` whenever any value is updated.
All parameter panel widgets bind to this model (read and write).

---

## KiKitRunner (QThread)

```python
class KiKitRunner(QThread):
    finished = Signal(Path)   # path to output .kicad_pcb
    failed = Signal(str)      # error message

    def run(self):
        # 1. Write config to temp JSON file
        # 2. Call kikit Python API:
        #    from kikit.panelize_ui import panelizePreset
        #    panelizePreset(preset, board_path, output_path)
        # 3. Emit finished(output_path) or failed(message)
```

`RunCoordinator` owns the debounce QTimer (600ms) and ensures only one runner
is active at a time. If config changes while running, queues one more run on
completion.

---

## Canvas Interaction

### Tab handles
- Rendered as small colored rectangles overlaid on board edges
- Draggable along the edge they're on (constrained movement)
- On drag end: promote tab spec to `type: fixed`, write coordinate to config
- Right-click context menu: "Delete tab", "Reset to auto", "Add tab here"

### Fiducial handles
- Rendered as crosshair + circle overlay
- Draggable anywhere within the panel frame area
- On drag end: update hoffset/voffset in config
- Right-click context menu: "Remove fiducial", "Reset to corner"

### Canvas context menu (right-click on empty space near board edge)
- "Add tab here" → adds fixed tab at click point

### Zoom / pan
- Middle mouse drag: pan
- Ctrl+wheel: zoom
- Ctrl+0: fit panel in view

---

## Parameter Panel Binding

Each param panel widget (spinbox, combobox, checkbox) connects to `ConfigModel`:
- Widget value change → `ConfigModel.set(section, key, value)`
- `ConfigModel.config_changed` → widgets refresh (to avoid loops, use blockSignals)
- `ConfigModel.config_changed` → `RunCoordinator.schedule_run()`

---

## File Operations

| Action | Behavior |
|--------|---------|
| Launch from pcbnew | Plugin reads `pcbnew.GetBoard().GetFileName()` → passes to subprocess |
| Board path received | Stored in ConfigModel; first KiKit run fires automatically |
| New config | Reset ConfigModel to KiKit defaults (board path retained) |
| Open config | Load `.kikit.json` → populate ConfigModel → trigger run |
| Save config | Write ConfigModel → `<boardname>.kikit.json` alongside the source PCB |
| Save config as | File dialog → save to any location |
| Export panel | Run KiKit → save output `.kicad_pcb`; default name `<boardname>-panel.kicad_pcb` |

The `.kikit.json` config file is saved next to the source `.kicad_pcb` so it
travels with the project. It is a valid KiKit config the user can also run from
the CLI directly.

---

## Reuse from PanelEditor

| Component | Status |
|-----------|--------|
| `pcbnew_renderer.py` | Copy verbatim, adapt imports |
| `pyproject.toml` structure | Adapt (new name, same deps minus kikit-python IPC) |
| `.vscode/settings.json` | Copy verbatim |
| PySide6 canvas pan/zoom pattern | Re-implement (simpler, no drag-drop) |
| Ruff config | Copy verbatim |

---

## Phase Plan

### Phase 1 — Scaffold & Render (current)
- [ ] Project structure, pyproject.toml, venv
- [ ] Copy/adapt pcbnew_renderer.py
- [ ] ConfigModel with KiKit defaults
- [ ] KiKitRunner QThread + RunCoordinator debounce
- [ ] PanelView/Scene: display rendered SVG layers
- [ ] main.py accepts board_path as argv[1]; auto-runs KiKit on launch
- [ ] Status bar: show run state (idle / running / error)
- [ ] pcbnew plugin skeleton (ActionPlugin + subprocess launch + icon)

### Phase 2 — Parameter Panels
- [ ] Layout panel (rows, cols, spacing, rotation)
- [ ] Tabs panel (type, width, count)
- [ ] Cuts panel (mousebites, vcuts)
- [ ] Framing panel (frame, rails)
- [ ] Tooling + Fiducials panels
- [ ] Text + Post panels
- [ ] Save/load KiKit JSON config

### Phase 3 — Interactive Canvas
- [ ] Tab handle items: overlay, drag-to-reposition, promote to fixed
- [ ] Tab context menus
- [ ] Fiducial handle items: overlay, drag
- [ ] Fiducial context menus
- [ ] Canvas context menu: "Add tab here"

### Phase 4 — Polish
- [ ] Layer visibility toggles (show/hide Cu, Silkscreen, etc.)
- [ ] Fit-to-view on new panel
- [ ] Error display (KiKit errors → friendly message in UI, not just console)
- [ ] Recently opened files
- [ ] Keyboard shortcuts

---

## Plugin Installation (Development)

For development, the plugin folder is junction-linked into KiCad's system scripting path:

**Windows:** `C:\Users\Sean\Documents\KiCad\9.0\scripting\plugins\kikit_viewer\`

```
kikit_viewer\        ← junction → <repo>\plugin\
├── __init__.py      # registers the ActionPlugin on import
└── kikit_viewer_plugin.py
```

Because the repo lives on a different drive from the KiCad user directory, a
directory junction won't work. Instead, `install_plugin.bat` copies a small stub
`__init__.py` into the KiCad plugin folder. The stub adds the repo's `plugin\`
directory to `sys.path` at load time, so all edits to `plugin\` are live without
any re-install step. Run it once, then restart KiCad.

`ActionPlugin` registration:
```python
# plugin/__init__.py
import pcbnew
from .kikit_viewer_plugin import KiKitViewerPlugin

KiKitViewerPlugin().register()
```

Icon requirements: 24×24 px PNG, referenced via `GetIconFileName(bool large)`
returning the absolute path. KiCad shows it in the toolbar.

PCM packaging (`metadata.json`, versioned zip) is deferred to Phase 5 (Release).

---

## Out of Scope (v1)

- Mixed-board panels (multiple different source PCBs)
- Live KiCad IPC sync (pcbnew reloads the panel PCB on demand)
- DRC on output panel
- Undo/redo (config changes are immediately re-run; history is the KiKit config file)
