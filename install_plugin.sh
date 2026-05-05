#!/usr/bin/env bash
# Install the KiKitViewer plugin into KiCad's user scripting path.
#
# A small stub __init__.py is written into the KiCad plugin folder. The stub
# adds the actual source tree to sys.path at load time, so all edits to
# plugins/ in this repo are live immediately without reinstalling.

set -euo pipefail

PLUGIN_NAME="kikit_viewer"
SOURCE_DIR="$(cd "$(dirname "$0")/plugins" && pwd)"

# Locate the KiCad scripting plugins directory for this platform
if [[ "$OSTYPE" == "darwin"* ]]; then
    KICAD_VER="${KICAD_VER:-9.0}"
    KICAD_PLUGINS="$HOME/Library/Preferences/kicad/$KICAD_VER/scripting/plugins"
else
    # Linux (and other Unix-likes)
    KICAD_VER="${KICAD_VER:-9.0}"
    KICAD_PLUGINS="$HOME/.local/share/kicad/$KICAD_VER/scripting/plugins"
fi

PLUGIN_DIR="$KICAD_PLUGINS/$PLUGIN_NAME"

mkdir -p "$PLUGIN_DIR"

echo "Writing stub to:"
echo "  $PLUGIN_DIR/__init__.py"

cat > "$PLUGIN_DIR/__init__.py" <<EOF
import sys as _sys
_src = '$SOURCE_DIR'
if _src not in _sys.path:
    _sys.path.insert(0, _src)
from kikit_viewer_plugin import KiKitViewerPlugin
KiKitViewerPlugin().register()
EOF

echo ""
echo "Done. The plugin stub points to:"
echo "  $SOURCE_DIR"
echo ""
echo "Restart KiCad to load the plugin and see the toolbar button."
