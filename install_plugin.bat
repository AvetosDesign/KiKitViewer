@echo off
:: Install the KiKitViewer plugin into KiCad's user scripting path.
::
:: Because the source is on a different drive from KiCad's plugin directory,
:: a junction won't work. Instead, a small stub __init__.py is copied into the
:: KiCad plugin folder. The stub adds the actual source tree to sys.path at
:: load time, so all edits to plugin\ on this drive are live immediately.

setlocal

set KICAD_PLUGINS=C:\Users\Sean\Documents\KiCad\9.0\scripting\plugins
set PLUGIN_NAME=kikit_viewer
set PLUGIN_DIR=%KICAD_PLUGINS%\%PLUGIN_NAME%
set SOURCE_DIR=%~dp0plugins

if not exist "%KICAD_PLUGINS%" (
    echo Creating KiCad plugins directory...
    mkdir "%KICAD_PLUGINS%"
)

if not exist "%PLUGIN_DIR%" (
    mkdir "%PLUGIN_DIR%"
)

echo Writing stub to:
echo   %PLUGIN_DIR%\__init__.py

(
    echo import sys as _sys
    echo _src = r'%SOURCE_DIR%'
    echo if _src not in _sys.path:
    echo     _sys.path.insert(0, _src^)
    echo from kikit_viewer_plugin import KiKitViewerPlugin
    echo KiKitViewerPlugin(^).register(^)
) > "%PLUGIN_DIR%\__init__.py"

echo.
echo Done. The plugin stub points to:
echo   %SOURCE_DIR%
echo.
echo Restart KiCad to load the plugin and see the toolbar button.
pause
