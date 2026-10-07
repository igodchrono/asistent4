# -*- coding: utf-8 -*-
"""llm_provider — переключение между локальной LLM (LM Studio) и внешним API (proxyapi.ru).

Настройки:
- provider: local | external
- external_api_url: URL внешнего API (OpenAI-совместимый)
- external_api_key: API ключ
- external_model: имя модели у внешнего провайдера
- local_api_url: URL локального сервера (LM Studio)
- local_model: имя локальной модели
"""
from __future__ import annotations
from typing import Any, Optional

from core.plugin_api import AppContext, HookResult, Plugin, SettingField
from core.llm_client import LLMClient


class PluginImpl(Plugin):
    id = "llm_provider"
    name = "Провайдер LLM"
    version = "1.0.0"
    description = "Выбор: локальная модель (LM Studio) или внешний API (proxyapi.ru)"
    settings_tab = "own"
    settings_tab_title = "Провайдер LLM"
    settings_schema = [
        SettingField(
            "provider", "Провайдер", "choice", "local",
            choices=["local", "external"],
            help="local = LM Studio на локальном ПК, external = внешний API (proxyapi.ru)",
        ),
        SettingField(
            "external_api_url", "External API URL", "str",
            "https://api.proxyapi.ru",
            help="OpenAI-совместимый endpoint. Например: https://api.proxyapi.ru",
        ),
        SettingField(
            "external_api_key", "External API Key", "str", "",
            help="Ключ от proxyapi.ru (получить на https://console.proxyapi.ru)",
        ),
        SettingField(
            "external_model", "External модель", "str", "gpt-4o",
            help="Имя модели: gpt-4o, gpt-4o-mini, claude-3-opus, claude-3-sonnet, "
                 "deepseek-chat, gemini-pro и др.",
        ),
        SettingField(
            "local_api_url", "Local API URL", "str",
            "http://127.0.0.1:1234/v1",
            help="Адрес LM Studio или другого локального OpenAI-сервера",
        ),
        SettingField(
            "local_model", "Local модель", "str", "local-model",
            help="Имя модели в LM Studio (как в списке загруженных)",
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

        # переключение на внешний API
        if low in ("переключи на внешний", "используй внешний", "переключи на proxyapi",
                   "используй proxyapi", "переключи на external", "используй external"):
            self._switch_provider(app, "external")
            return HookResult(True, "Переключено на внешний API.")

        # переключение на локальную модель
        if low in ("переключи на локальный", "используй локальный", "на локальный",
                   "вернись на локальный", "переключи на local", "используй local",
                   "вернись на local", "переключи на lm studio", "используй lm studio"):
            self._switch_provider(app, "local")
            return HookResult(True, "Переключено на локальную модель.")

        return None

    def _switch_provider(self, app: AppContext, target: str) -> None:
            """Переключает провайдера и пересоздаёт LLMClient."""
            current = app.get_plugin_setting(self.id, "provider", "local")
            if current == target:
                return  # on_user_message уже вернул HookResult

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
            engine.llm = LLMClient.from_config(app.config, app)
            app.llm = engine.llm
            self._log_provider(app)

    @staticmethod
    def _log_provider(app: AppContext) -> None:
        prov = app.get_plugin_setting("llm_provider", "provider", "local")
        if prov == "external":
            model = app.get_plugin_setting("llm_provider", "external_model", "?")
            url = app.get_plugin_setting("llm_provider", "external_api_url", "?")
            print(f"🔌 LLM провайдер: external ({model}) @ {url}", flush=True)
        else:
            url = app.get_plugin_setting("llm_provider", "local_api_url", "?")
            model = app.get_plugin_setting("llm_provider", "local_model", "?")
            print(f"🔌 LLM провайдер: local ({model}) @ {url}", flush=True)


def register():
    return PluginImpl()