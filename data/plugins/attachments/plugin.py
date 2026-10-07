# -*- coding: utf-8 -*-
"""Анализ вложений из data/attachments + state pending_attachments."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from core.plugin_api import AppContext, Plugin, SettingField

_TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log", ".py", ".ini", ".yaml", ".yml", ".xml", ".html"}
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


class PluginImpl(Plugin):
    id = "attachments"
    name = "Вложения"
    version = "1.0.0"
    description = "Текстовый разбор файлов из чата для LLM"
    settings_tab = "own"
    settings_tab_title = "Вложения"
    settings_schema = [
        SettingField("enabled", "Включить разбор файлов", "bool", True),
        SettingField("max_chars", "Макс. символов из файла", "int", 6000, min_value=500, max_value=30000),
    ]

    def on_load(self, app: AppContext) -> None:
        print("📎 attachments 1.0: inbox data/attachments", flush=True)

    def on_before_llm(self, messages: List[Dict[str, Any]], app: AppContext) -> List[Dict[str, Any]]:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return messages
        files = list(app.state.get("pending_attachments") or [])
        if not files or not messages:
            return messages
        chunks = []
        limit = int(app.get_plugin_setting(self.id, "max_chars", 6000) or 6000)
        for raw in files:
            p = Path(str(raw))
            if not p.exists():
                chunks.append(f"- нет файла: {p}")
                continue
            ext = p.suffix.lower()
            if ext in _IMAGE_EXT:
                chunks.append(f"- картинка: {p.name} путь={p}")
                continue
            if ext in _TEXT_EXT:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    chunks.append(f"- {p.name}: не прочитан ({e})")
                    continue
                chunks.append(f"- файл {p.name}:\n{text[:limit]}")
                continue
            chunks.append(f"- бинарный файл {p.name} ({p.stat().st_size} байт), путь={p}")
        block = "\n\n[ВЛОЖЕНИЯ ДЛЯ АНАЛИЗА]\n" + "\n".join(chunks) + "\nОпиши/разбери то, что во вложениях, если пользователь об этом просит.\n"
        if messages[0].get("role") == "system":
            messages[0]["content"] = str(messages[0].get("content") or "") + block
        app.state["pending_attachments"] = []
        return messages


def register():
    return PluginImpl()
