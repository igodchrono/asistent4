# -*- coding: utf-8 -*-
"""image_picker — умный выбор изображений с экрана с памятью предпочтений.

Сценарий активируется по триггерам («посмотри на экран и выбери»,
«что там интересного», «выбери картинку», «какая из них лучше»,
«покажи ещё похожее» и т.д.) и после публикации crop ждёт реакцию
(👍/👎/«нравится»/«не то»), которая обновляет PreferenceMemory.

Использует:
    - ScreenAnalyzer  (plugins.image_picker.screen_analyzer)
    - PreferenceMemory (core.preference_memory)
    - SmartPicker     (plugins.image_picker.smart_picker)
    - FeedbackParser  (core.feedback_parser)
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.plugin_api import AppContext, HookResult, Plugin, SettingField
from core.feedback_parser import FeedbackParser
from core.preference_memory import PreferenceMemory

# Триггеры активации (БЛОК 9.5)
_PICK_TRIGGERS = (
    "посмотри на экран и выбери", "выбери с экрана", "выбери что-то",
    "выбери картинк", "выбери одну", "выбери из экран", "выбери на экране",
    "выбери фото", "выбери изображ",
    "что там интересного", "что интересного на экране",
    "какая из них лучше", "какая лучше", "какая нравится", "какая тебе нрав",
    "покажи что-то с экрана", "покажи мне что-то с экрана",
    "найди на экране", "вырежи и покажи", "вырежи картин",
    "покажи что-нибудь с экрана", "что-нибудь с экрана",
    "покажи картинку с экрана", "покажи фото с экрана",
    "выбери не как в прошлый раз", "не как в прошлый раз",
)
_PICK_KEYWORDS = (
    "выбери", "выбрать", "вырежи", "какая нрав", "какая лучше",
    "что там интересного", "что-то с экрана", "что-нибудь с экрана",
)
_SIMILAR_TRIGGERS = (
    "покажи ещё похожее", "покажи еще похожее", "ещё такое",
    "еще такое", "ещё похожее", "еще похожее", "в том же стиле",
    "похожее на то, что я смотрел", "мне нравится что-то похожее",
)

_SKIP = ("нарисуй", "сгенерир", "сделай картин", "погугли", "поищи", "открой")


class PluginImpl(Plugin):
    id = "image_picker"
    name = "Умный выбор с экрана"
    version = "1.0.0"
    description = "Выбор картинок с экрана по предпочтениям + обратная связь"
    settings_tab = "plugins"
    settings_tab_title = "Умный выбор"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("min_region_size", "Мин. сторона области (px)", "int", 100,
                     min_value=40, max_value=500),
        SettingField("max_regions", "Макс. регионов", "int", 50,
                     min_value=5, max_value=200),
        SettingField("diversity_penalty", "Штраф за повтор", "float", 0.3,
                     min_value=0.0, max_value=1.0),
        SettingField("crop_padding_px", "Рамка crop (px)", "int", 4,
                     min_value=0, max_value=40),
        SettingField("feedback_window_seconds", "Окно обратной связи (сек)", "int", 30,
                     min_value=5, max_value=300),
    ]

    def __init__(self):
        self.app = None
        self.analyzer = None
        self.memory = None
        self.picker = None
        self._feedback = FeedbackParser()
        self._last_pick: Optional[Dict[str, Any]] = None  # {path, region, ts}

    # ── Жизненный цикл ────────────────────────────────────────────────

    def on_load(self, app: AppContext) -> None:
        self.app = app
        app.state["image_picker_plugin"] = self
        self._init_components(app)

    def _init_components(self, app: AppContext) -> None:
        from plugins.image_picker.screen_analyzer import ScreenAnalyzer
        from plugins.image_picker.smart_picker import SmartPicker

        min_size = int(app.get_plugin_setting(self.id, "min_region_size", 100) or 100)
        max_reg = int(app.get_plugin_setting(self.id, "max_regions", 50) or 50)
        self.analyzer = ScreenAnalyzer(
            min_region_size=min_size,
            max_regions=max_reg,
        )
        self.memory = PreferenceMemory()
        self.picker = SmartPicker(
            analyzer=self.analyzer,
            memory=self.memory,
            persona=self._persona_dict(app),
            diversity_penalty=float(
                app.get_plugin_setting(self.id, "diversity_penalty", 0.3) or 0.3
            ),
            crop_padding_px=int(
                app.get_plugin_setting(self.id, "crop_padding_px", 4) or 4
            ),
        )

    def _persona_dict(self, app: AppContext) -> Dict[str, Any]:
        """Собрать описание персонажа для persona_score."""
        cid = "default"
        try:
            if hasattr(app, "get_active_character"):
                cid = str(app.get_active_character() or "") or cid
            if not cid or cid == "персонаж":
                cid = str(getattr(app.config, "ACTIVE_CHARACTER", "") or "") or cid
        except Exception:
            pass
        card = ""
        try:
            from character_catalog import read_character_card
            card = (read_character_card(cid) or "")[:1500]
        except Exception:
            card = ""
        return {"name": cid, "card": card}

    # ── Обработка сообщений ──────────────────────────────────────────

    def on_user_message(self, text: str, app: AppContext) -> Optional[HookResult]:
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        low = (text or "").strip().lower()
        if not low:
            return None

        # 1. Обратная связь после публикации pick
        if self._last_pick is not None:
            fb = self._feedback.parse(low)
            if fb.kind in ("positive", "negative", "again", "similar", "reset"):
                return self._handle_feedback(low, fb, app)

        # 2. Триггеры pick / similar
        if any(w in low for w in _SKIP):
            return None
        if any(w in low for w in _SIMILAR_TRIGGERS):
            self._launch_pick_many(app, 3)
            return HookResult(True, "ищу похожее на то, что вы любите…")

        # 3. Прямой запрос на pick:  «выбери что-то» — без слова «экран»
        if self._wants_pick(low):
            self._launch_pick(app, low)
            return HookResult(True, "смотрю экран и выбираю…")

        return None

    @staticmethod
    def _wants_pick(low: str) -> bool:
        """Распознать триггер выбора с экрана."""
        for t in _PICK_TRIGGERS:
            if t in low:
                return True
        if any(k in low for k in _PICK_KEYWORDS):
            if any(w in low for w in ("экран", "монитор", "картин", "фото",
                                       "изображ", "с экрана", "скрин")):
                return True
        return False

    # ── Обратная связь ───────────────────────────────────────────────

    def _handle_feedback(
        self, low: str, fb: Any, app: AppContext
    ) -> Optional[HookResult]:
        last = self._last_pick or {}
        if fb.kind == "reset":
            self.memory.reset()
            self._last_pick = None
            return HookResult(True, "Ок, предпочтения сброшены.")
        if fb.kind == "again":
            self.memory.record_choice(
                chosen=last.get("region"),
                rejected=None,
                explicit_feedback="давай другое",
                request_text="",
            )
            self._launch_pick(app, "другое", exclude_last=True)
            return HookResult(True, "ок, ищу другую.")
        if fb.kind == "similar":
            self.memory.record_choice(
                chosen=last.get("region"),
                explicit_feedback="покажи ещё похожее",
                request_text="",
            )
            self._launch_pick_many(app, 3)
            return HookResult(True, "ищу три похожих…")
        if fb.kind in ("positive", "negative"):
            self.memory.record_choice(
                chosen=last.get("region"),
                explicit_feedback=low,
                request_text="",
            )
            msg = (
                "Запомнила — вам нравится такое направление."
                if fb.kind == "positive"
                else "Поняла, больше такое не предлагаю."
            )
            return HookResult(True, msg)
        return None

    # ── Запуск пика в потоке ─────────────────────────────────────────

    def _launch_pick(
        self, app: AppContext, request: str, exclude_last: bool = False
    ) -> None:
        threading.Thread(
            target=self._pick_thread,
            args=(app, request, exclude_last),
            name="image-picker",
            daemon=True,
        ).start()

    def _launch_pick_many(self, app: AppContext, n: int) -> None:
        threading.Thread(
            target=self._pick_many_thread,
            args=(app, n),
            name="image-picker-many",
            daemon=True,
        ).start()

    def _pick_thread(self, app: AppContext, request: str, exclude_last: bool) -> None:
        try:
            asyncio.run(self._run_pick(app, request, exclude_last))
        except Exception as e:
            print(f"image_picker: {e}", flush=True)

    async def _run_pick(
        self, app: AppContext, request: str, exclude_last: bool
    ) -> None:
        if exclude_last and self._last_pick:
            region = self._last_pick.get("region")
            if region is not None and hasattr(region, "bbox"):
                self.picker.exclude_bboxes.append(tuple(region.bbox))
        result = self.picker.pick(request)
        self._publish(app, result)

    def _pick_many_thread(self, app: AppContext, n: int) -> None:
        try:
            results = self.picker.pick_many("похожее", n)
            self._publish_many(app, results)
        except Exception as e:
            print(f"image_picker many: {e}", flush=True)

    # ── Публикация в чат ─────────────────────────────────────────────

    def _publish(self, app: AppContext, result: Any) -> None:
        """Опубликовать crop в чат."""
        if result is None or result.region is None or getattr(result, "reason", "") == "no_regions":
            msg = "Не нашла на экране отдельных изображений."
            gui = getattr(app, "window", None) or app.state.get("gui")
            if gui is not None and hasattr(gui, "publish_assistant_message"):
                gui.publish_assistant_message(msg)
            return

        top_tags = self.memory.get_top_tags(3)
        tag_str = ", ".join(tag for tag, _w in top_tags) if top_tags else "не определились"
        why = result.reason or "просто хорошо выглядит"
        msg = f"🖼 Выбрала вот эту: {why}.\nУчла ваши предпочтения: {tag_str}"

        crop_path = result.crop_path
        if crop_path and Path(str(crop_path)).is_file():
            msg += f"\n[фото: {crop_path}]"

        self._last_pick = {
            "path": str(crop_path) if crop_path else None,
            "region": result.region,
            "ts": time.time(),
        }
        if crop_path:
            app.state["phone_media_last"] = str(crop_path)

        gui = getattr(app, "window", None) or app.state.get("gui")
        if gui is not None and hasattr(gui, "publish_assistant_message"):
            gui.publish_assistant_message(msg)
        else:
            print(f"[image_picker] {msg}", flush=True)

    def _publish_many(self, app: AppContext, results: List[Any]) -> None:
        gui = getattr(app, "window", None) or app.state.get("gui")
        if not results:
            if gui is not None and hasattr(gui, "publish_assistant_message"):
                gui.publish_assistant_message("Похожих изображений на экране не нашла.")
            return
        lines = ["Три варианта в том же стиле:"]
        for r in results:
            if r.crop_path and Path(str(r.crop_path)).is_file():
                lines.append(f"[фото: {r.crop_path}]")
        msg = "\n".join(lines)
        if gui is not None and hasattr(gui, "publish_assistant_message"):
            gui.publish_assistant_message(msg)

    # ── Инструменты для ядра ─────────────────────────────────────────

    def register_tools(self, app: AppContext) -> None:
        app.tools["pick_from_screen"] = self.tool_pick_from_screen
        app.tools["pick_similar_to_past"] = self.tool_pick_similar

    def tool_pick_from_screen(self, app: AppContext, **kwargs: Any) -> str:
        """Выбрать изображение с экрана по предпочтениям."""
        request = str(kwargs.get("request") or kwargs.get("query") or "выбери что-то")
        self._launch_pick(app, request)
        return "Смотрю экран и выбираю картинку…"

    def tool_pick_similar(self, app: AppContext, **kwargs: Any) -> str:
        """Показать топ похожих изображений."""
        n = int(kwargs.get("n") or 3)
        self._launch_pick_many(app, n)
        return "Ищу похожие…"


def register():
    return PluginImpl()