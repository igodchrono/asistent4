# -*- coding: utf-8 -*-
from __future__ import annotations
from pathlib import Path
from core.plugin_api import AppContext, Plugin, SettingField

class PluginImpl(Plugin):
    id = "character_log"
    name = "Лог смены персонажа"
    version = "2.0.0"
    settings_tab = "own"
    settings_tab_title = "Смена персонажа"
    settings_schema = [
        SettingField("enabled", "Включить лог смены", "bool", True),
    ]

    def on_load(self, app: AppContext) -> None:
        cid = getattr(app.config, "ACTIVE_CHARACTER", "") or ""
        if cid:
            self.on_character_changed(str(cid), "", app)

    def on_character_changed(self, character_id: str, previous_id: str, app: AppContext) -> None:
        root = Path(getattr(app.config, "DATA_DIR", Path("data")))
        path = root / "personas" / "characters" / str(character_id)
        print(f"character_log: HOOK {previous_id} → {character_id} path={path}", flush=True)
        # nsfw flag from card
        try:
            from core._guard import character_is_nsfw
            app.state["character_nsfw"] = bool(character_is_nsfw(app))
        except Exception:
            app.state["character_nsfw"] = False

def register():
    return PluginImpl()
