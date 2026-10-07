# -*- coding: utf-8 -*-
"""Периодические инициативные сообщения ассистента (с fallback без LLM)."""
from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Any, Optional

from core.plugin_api import AppContext, Plugin, SettingField


class PluginImpl(Plugin):
    id = "auto_messages"
    name = "Авто-сообщения"
    version = "1.1.0"
    description = "Периодически шлёт короткие сообщения. Если LLM недоступен — готовые фразы."
    settings_tab = "own"
    settings_tab_title = "Авто-сообщения"
    settings_schema = [
        SettingField("enabled", "Включить авто-сообщения", "bool", False),
        SettingField("interval_minutes", "Период между авто-сообщениями (мин)", "int", 10, min_value=1, max_value=1440),
        SettingField("idle_minutes", "Только после простоя чата (мин)", "int", 5, min_value=1, max_value=120,
                     help="Не писать, пока пользователь активно переписывается."),
        SettingField("chance_percent", "Вероятность сообщения (%)", "int", 50, min_value=0, max_value=100),
        SettingField("quiet_start", "Начало тихого времени", "int", 23, min_value=0, max_value=23),
        SettingField("quiet_end", "Конец тихого времени", "int", 8, min_value=0, max_value=23),
        SettingField("allow_flirty", "Разрешить лёгкий флирт", "bool", True),
        SettingField("use_llm", "Генерировать через LLM (иначе готовые фразы)", "bool", True),
        SettingField("debug_log", "Лог в консоль", "bool", True),
    ]

    _MESSAGES = {
        "morning": (
            ("happy", "Доброе утро. Как настроение сегодня?"),
            ("sleepy", "Доброе утро… Ты уже проснулся или мне ещё немного подождать?"),
            ("happy", "Утро. Я рядом — если нужно, просто напиши."),
        ),
        "day": (
            ("thinking", "Как проходит день? Не забывай сделать небольшой перерыв."),
            ("tired", "Ты давно не отвлекался? Может, немного передохнёшь?"),
            ("sad", "Я рядом. Если день тяжёлый, можешь рассказать, что случилось."),
            ("happy", "Эй. Как дела? Могу просто поболтать."),
        ),
        "evening": (
            ("happy", "Вечер уже наступил. Как прошёл твой день?"),
            ("tired", "Похоже, день был долгим. Постарайся сегодня немного отдохнуть."),
            ("flirty", "Вечер располагает к тёплой компании. Я могу немного побыть рядом."),
            ("happy", "Вечер. Есть минутка? Хочу узнать, как ты."),
        ),
        "night": (
            ("sleepy", "Уже поздно. Может, пора отложить дела и немного поспать?"),
            ("tired", "Ты ещё не спишь? Береги себя, завтра понадобится энергия."),
            ("flirty", "Тихая ночь… Можно просто немного поговорить наедине."),
            ("sleepy", "Ночь. Я тут, если не хочется оставаться одному."),
        ),
    }

    def __init__(self) -> None:
        self.app: Optional[AppContext] = None
        self._timer = None
        self._last_message_at = 0.0
        self._last_text = ""
        self._task = None
        self._loop = None

    def on_load(self, app: AppContext) -> None:
        self.app = app
        app.state["auto_messages_plugin"] = self
        self._start_timer(app)
        en = bool(app.get_plugin_setting(self.id, "enabled", False))
        mins = int(app.get_plugin_setting(self.id, "interval_minutes", 5) or 5)
        print(
            f"💬 auto_messages 1.1: {'ON' if en else 'OFF'} interval={mins}m "
            f"(включи во вкладке «Авто-сообщения»)",
            flush=True,
        )

    def on_shutdown(self, app: AppContext) -> None:
        if self._timer is not None:
            try:
                self._timer.stop()
                self._timer.deleteLater()
            except Exception:
                pass
            self._timer = None
        if self._task is not None and not getattr(self._task, "done", lambda: True)():
            try:
                self._task.cancel()
            except Exception:
                pass

    def on_character_changed(self, character_id: str, previous_id: str, app: AppContext) -> None:
        self.app = app
        self._restart_timer(app)

    def _start_timer(self, app: AppContext) -> None:
        try:
            from PyQt5 import QtCore
        except ImportError:
            print("auto_messages: PyQt5 недоступен, таймер не запущен", flush=True)
            return
        self._timer = QtCore.QTimer()
        self._timer.timeout.connect(lambda: self._tick(app))
        self._restart_timer(app)

    def _restart_timer(self, app: AppContext) -> None:
        if self._timer is None:
            return
        minutes = int(app.get_plugin_setting(self.id, "interval_minutes", 5) or 5)
        self._timer.start(max(1, minutes) * 60 * 1000)

    def _log(self, app: AppContext, msg: str) -> None:
        if app.get_plugin_setting(self.id, "debug_log", True):
            print(f"auto_messages: {msg}", flush=True)

    def _tick(self, app: AppContext) -> None:
        if not app.get_plugin_setting(self.id, "enabled", False):
            return
        window = getattr(app, "window", None)
        if window is None:
            self._log(app, "skip: no window")
            return
        if getattr(window, "_busy", False):
            self._log(app, "skip: busy")
            return
        # простой чата: last_user_activity
        idle_need = int(app.get_plugin_setting(self.id, "idle_minutes", 5) or 5) * 60
        last = float(app.state.get("last_user_activity") or app.state.get("last_chat_activity") or 0)
        import time as _time
        if last and (_time.time() - last) < idle_need:
            self._log(app, f"skip: chat active idle={int(_time.time()-last)}s need>={idle_need}s")
            return
        try:
            visible = window.isVisible() if callable(getattr(window, "isVisible", None)) else True
        except Exception:
            visible = True
        if not visible:
            self._log(app, "skip: window not visible")
            return
        if self._quiet_time(app):
            self._log(app, "skip: quiet hours")
            return
        chance = int(app.get_plugin_setting(self.id, "chance_percent", 50) or 0)
        roll = random.randrange(100)
        if roll >= max(0, min(100, chance)):
            self._log(app, f"skip: chance roll={roll} need<{chance}")
            return

        emotion, static_text = self._choose_message(app)
        self._last_message_at = datetime.now().timestamp()
        self._apply_emotion(app, emotion)

        use_llm = bool(app.get_plugin_setting(self.id, "use_llm", True))
        engine = getattr(window, "engine", None) or app.state.get("engine")

        if use_llm and engine is not None and hasattr(engine, "generate_proactive"):
            self._run_async(self._generate(engine, window, app, emotion, static_text))
            return

        # fallback — готовая фраза
        self._publish(window, app, static_text)

    def _publish(self, window: Any, app: AppContext, text: str) -> None:
        text = (text or "").strip()
        if not text or text == self._last_text:
            return
        if getattr(window, "_busy", False):
            return
        self._last_text = text
        if hasattr(window, "publish_assistant_message"):
            try:
                window.publish_assistant_message(text)
                self._log(app, f"sent: {text[:80]!r}")
                return
            except Exception as e:
                self._log(app, f"publish failed: {e}")
        # запасной путь
        try:
            if hasattr(window, "append_assistant"):
                window.append_assistant(text)
                self._log(app, f"append: {text[:80]!r}")
        except Exception as e:
            self._log(app, f"no publish path: {e}")

    def _run_async(self, coro) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._task = asyncio.ensure_future(coro)
            else:
                loop.run_until_complete(coro)
        except RuntimeError:
            # нет loop в потоке Qt — новый
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(coro)
            except Exception as e:
                print(f"auto_messages: async failed: {e}", flush=True)
                # fallback sync static already passed into coro
        except Exception as e:
            print(f"auto_messages: async error: {e}", flush=True)

    async def _generate(
        self, engine: Any, window: Any, app: AppContext, emotion: str, static_text: str
    ) -> None:
        period = self._period()
        user_mood = str(app.state.get("emotion", "neutral"))
        instruction = (
            "Напиши одно короткое инициативное сообщение пользователю от лица ассистента. "
            f"Сейчас {period}, текущая эмоция ассистента: {emotion}, настроение пользователя: {user_mood}. "
            "Сообщение должно быть естественным, тёплым и уместным, без упоминания правил, "
            "таймера или генерации. Не используй откровенный сексуальный текст; допустим лёгкий флирт. "
            "1–2 предложения."
        )
        text = ""
        try:
            text = await engine.generate_proactive(instruction)
        except Exception as exc:
            self._log(app, f"LLM failed → static: {exc}")
        if not (text or "").strip():
            text = static_text
        self._publish(window, app, text)

    def _choose_message(self, app: AppContext) -> tuple[str, str]:
        period = self._period()
        period_key = {"утро": "morning", "день": "day", "вечер": "evening", "ночь": "night"}[period]
        options = list(self._MESSAGES[period_key])
        if not app.get_plugin_setting(self.id, "allow_flirty", True):
            options = [item for item in options if item[0] != "flirty"] or options
        current = str(app.state.get("emotion", "neutral"))
        matching = [item for item in options if item[0] == current]
        return random.choice(matching or options)

    @staticmethod
    def _period() -> str:
        hour = datetime.now().hour
        if 5 <= hour < 11:
            return "утро"
        if 11 <= hour < 18:
            return "день"
        if 18 <= hour < 23:
            return "вечер"
        return "ночь"

    @staticmethod
    def _apply_emotion(app: AppContext, emotion: str) -> None:
        plugin = app.plugins.get("persona") or app.state.get("emotion_plugin") or app.plugins.get("emotion")
        if plugin is not None and hasattr(plugin, "set_context"):
            try:
                plugin.set_context(app, emotion, "auto_message")
            except Exception:
                pass

    @staticmethod
    def _quiet_time(app: AppContext) -> bool:
        hour = datetime.now().hour
        start = int(app.get_plugin_setting("auto_messages", "quiet_start", 23) or 0)
        end = int(app.get_plugin_setting("auto_messages", "quiet_end", 8) or 0)
        if start > end:
            return hour >= start or hour < end
        return start <= hour < end


def register() -> PluginImpl:
    return PluginImpl()


Plugin = PluginImpl
