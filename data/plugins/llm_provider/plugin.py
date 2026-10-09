# -*- coding: utf-8 -*-
"""llm_provider — переключение источника LLM.

Источники:
  - local      — LM Studio (локальный OpenAI-совместимый API)
  - external   — proxyapi.ru или другой внешний API
  - exe        — внешний .exe файл
"""
from __future__ import annotations
import os
from typing import Any, Optional

from core.plugin_api import AppContext, HookResult, Plugin, SettingField
from core.llm_manager import LLMManager


class PluginImpl(Plugin):
    id = "llm_provider"
    name = "Провайдер LLM"
    version = "1.1.0"
    description = "Источник LLM: API (LM Studio / proxyapi) или EXE"
    settings_tab = "own"
    settings_tab_title = "Провайдер LLM"
    settings_schema = [
        SettingField(
            "provider", "Источник LLM", "choice", "local",
            choices=["local", "external", "exe"],
            help="local = LM Studio, external = proxyapi.ru, exe = .exe файл",
        ),
        # ── API: external ─────────────────────────────────────────
        SettingField(
            "external_api_url", "External API URL", "str",
            "https://api.proxyapi.ru",
            help="OpenAI-совместимый endpoint (https://api.proxyapi.ru)",
        ),
        SettingField(
            "external_api_key", "External API Key", "str", "",
            help="Ключ от proxyapi.ru (https://console.proxyapi.ru)",
        ),
        SettingField(
            "external_model", "External модель", "str", "gpt-4o",
            help="gpt-4o, gpt-4o-mini, claude-3-opus, deepseek-chat, gemini-pro и др.",
        ),
        # ── EXE ───────────────────────────────────────────────────
        SettingField(
            "exe_path", "EXE путь", "str", "",
            help="Полный путь к .exe файлу для инференса",
        ),
    ]

    def __init__(self):
        self._current_provider: Optional[str] = None

    def on_load(self, app: AppContext) -> None:
        self.app = app
        prov = app.get_plugin_setting(self.id, "provider", "local")
        self._current_provider = prov
        self._log_provider(app)

    def on_user_message(self, text: str, app: AppContext) -> Optional[HookResult]:
        """Обработка команд смены провайдера прямо из чата."""
        low = (text or "").strip().lower()

        # ── переключение на external API ──────────────────────────
        if low in ("переключи на внешний", "используй внешний", "переключи на proxyapi",
                   "используй proxyapi", "переключи на external", "используй external"):
            self._switch_provider(app, "external")
            return HookResult(True, "✅ Переключено на внешний API (proxyapi).")

        # ── переключение на local API (LM Studio) ────────────────
        if low in ("переключи на локальный", "используй локальный", "на локальный",
                   "вернись на локальный", "переключи на local", "используй local",
                   "вернись на local", "переключи на lm studio", "используй lm studio"):
            self._switch_provider(app, "local")
            return HookResult(True, "✅ Переключено на локальный API (LM Studio).")

        # ── переключение на EXE ───────────────────────────────────
        if low in ("переключи на exe", "используй exe", "на exe", "переключи на исполняемый",
                   "используй исполняемый"):
            self._switch_provider(app, "exe")
            return HookResult(True, "✅ Переключено на EXE провайдер.")

        return None

    def _switch_provider(self, app: AppContext, target: str) -> None:
        """Переключает провайдера и пересоздаёт LLM через LLMManager."""
        current = app.get_plugin_setting(self.id, "provider", "local")
        if current == target:
            return

        engine = getattr(app, "engine", None)
        if engine is None:
            return

        # Сохраняем в config.PLUGIN_SETTINGS
        try:
            from settings_manager import save_settings
            if not hasattr(app.config, "PLUGIN_SETTINGS"):
                app.config.PLUGIN_SETTINGS = {}
            block = app.config.PLUGIN_SETTINGS.setdefault(self.id, {})
            block["provider"] = target
            save_settings({"PLUGIN_SETTINGS": app.config.PLUGIN_SETTINGS})
        except Exception as e:
            print(f"⚠️ llm_provider save: {e}", flush=True)

        self._current_provider = target
        engine.llm = LLMManager.from_config(app.config, app)
        app.llm = engine.llm
        self._log_provider(app)

    @staticmethod
    def _log_provider(app: AppContext) -> None:
        prov = app.get_plugin_setting("llm_provider", "provider", "local")
        if prov == "external":
            model = app.get_plugin_setting("llm_provider", "external_model", "?")
            url = app.get_plugin_setting("llm_provider", "external_api_url", "?")
            print(f"🔌 LLM: external ({model}) @ {url}", flush=True)
        elif prov == "exe":
            exe = app.get_plugin_setting("llm_provider", "exe_path", "")
            print(f"🔌 LLM: EXE ({exe})", flush=True)
        else:
            url = str(getattr(app.config, "API_URL", "?"))
            model = str(getattr(app.config, "MODEL_NAME", "?"))
            print(f"🔌 LLM: local ({model}) @ {url}", flush=True)


def register():
    return PluginImpl()