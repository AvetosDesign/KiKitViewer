from __future__ import annotations
from kikit_viewer.config.model import ConfigModel
from kikit_viewer.ui.params.base_panel import SectionPanel


class TextPanel(SectionPanel):
    def __init__(self, model: ConfigModel, parent=None) -> None:
        super().__init__("text", model, parent)
