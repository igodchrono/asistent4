# -*- coding: utf-8 -*-
from __future__ import annotations
from typing import Any, Dict, List
from core.plugin_api import AppContext, Plugin, SettingField

class PluginImpl(Plugin):
    id = "deep_think"
    name = "Глубокое размышление"
    version = "2.0.0"
    settings_tab = "own"
    settings_tab_title = "Глубокое мышление"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("max_tokens", "Макс. токенов (подробный ответ)", "int", 4096, min_value=512, max_value=16000),
    ]

    def on_load(self, app: AppContext) -> None:
        print("🧠 deep_think: loaded (intent tool)", flush=True)

    def register_tools(self, app: AppContext) -> None:
        app.tools["deep_think"] = self.tool_deep

    def tool_deep(self, app: AppContext, **kw) -> str:
        n = int(app.get_plugin_setting(self.id, "max_tokens", 4096) or 4096)
        app.state["llm_max_tokens"] = max(512, min(16000, n))
        return None  # type: ignore  # None → chat_engine продолжает обычный чат

    def on_before_llm(self, messages: List[Dict[str, Any]], app: AppContext) -> List[Dict[str, Any]]:
        if app.state.get("llm_max_tokens"):
            print("🧠 deep_think: ON", flush=True)
        return messages

def register():
    return PluginImpl()
