# -*- coding: utf-8 -*-
"""Генератор картинок: Qwen-Image 2.1. Текст → t2i, референс → edit.

«То же самое, но X»: продолжение последней генерации — предыдущий кадр
берётся как референс, предыдущий промт сохраняется целиком (якоря),
меняется только X. История генераций пишется в data/generations/history.jsonl.
"""
from __future__ import annotations

import copy
import json
import mimetypes
import random
import re
import asyncio
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from core.plugin_api import AppContext, HookResult, Plugin, SettingField

try:
    from core.continuation_detector import continuation_trigger, is_continuation, is_reset_request
    from core.generation_history import GenerationHistory, GenerationRecord, get_history
    from core.prompt_anchors import apply_anchors, anchors_text, check_prompt_drift, extract_anchors
except Exception:  # прагматика: без core плагин живёт без «то же самое»
    continuation_trigger = is_continuation = is_reset_request = None
    GenerationHistory = GenerationRecord = get_history = None
    apply_anchors = anchors_text = check_prompt_drift = extract_anchors = None

# настройки продолжения по умолчанию (перекрываются из data/settings.json)
_CONT_DEFAULTS = {
    "enabled": True,
    "detect_triggers": True,
    "use_last_as_reference": True,
    "preserve_seed": True,
    "anchor_extraction": True,
    "min_prompt_similarity": 0.6,
    "i2i_strength": 0.7,
}

_STRICT_CONT_SYSTEM = (
    "Ты — редактор промтов для генерации изображений. "
    "Тебе дан предыдущий промт и запрос на изменение. "
    "Верни НОВЫЙ полный промт (один английский абзац), где:\n"
    "- сохранены ВСЕ детали предыдущего: персонаж, одежда, материалы, "
    "мебель, окружение, стиль, освещение, композиция;\n"
    "- изменено только то, что просит пользователь;\n"
    "- длина НЕ меньше предыдущего промта;\n"
    "- композиция адаптирована под изменение («спиной» → view from behind, back to camera).\n"
    "Ответ — одна строка промта, без пояснений, без JSON."
)

_ASK = (
    "сгенерируй", "сгенерировать", "нарисуй", "нарисовать",
    "сделай картин", "сделай изображ", "сделай арт",
    "создай картин", "изобрази", "представь в виде",
    "сгенери изображение", "сгенерируй изображение", "сгенерируй картин",
    "нарисуй мне", "draw ", "generate an image", "generate a picture", "create an image",
)
_ALIASES = {
    "t2i": "workflows/qwen21_t2i.json",
    "текст": "workflows/qwen21_t2i.json",
    "qwen": "workflows/qwen21_t2i.json",
    "qwen21": "workflows/qwen21_t2i.json",
    "qwen_image": "workflows/qwen21_t2i.json",
    "edit": "workflows/qwen21_edit.json",
    "правка": "workflows/qwen21_edit.json",
    "qwen_edit": "workflows/qwen21_edit.json",
    "референс": "workflows/qwen21_edit.json",
    "i2i": "workflows/qwen21_i2i.json",
    "img2img": "workflows/qwen21_i2i.json",
    "ipadapter": "workflows/qwen21_i2i.json",
}
_EDIT_WORDS = (
    "по референс", "с референс", "используй референс", "это референс",
    "по фото", "по этому фото", "это фото", "по этой картин", "по картинке",
    "на основе фото", "на основе картин", "на основе изображ",
    "по прикреп", "по вложен", "с этим фото", "по образцу",
    "правк", "дорисуй", "перекрас", "отредактир",
    "переделай картин", "переделай фото", "переделай изображ",
    "измени фото", "измени картин", "измени изображ",
    "поправь фото", "поправь картин",
    "<image",
)
_PLAIN_WORDS = (
    "просто картин", "просто нарисуй", "просто сгенери",
    "без референс", "без фото", "с нуля", "по тексту", "только текст", "не по фото",
)
# «покажи себя», «хочу тебя увидеть» — не поиск картинок
_SEE_SELF = (
    "хочу тебя увидеть",
    "хочу увидеть тебя",
    "хочу тебя видеть",
    "хочу видеть тебя",
    "хочу на тебя посмотреть",
    "хочу посмотреть на тебя",
    "дай на тебя посмотреть",
    "дай посмотреть на тебя",
    "можно на тебя посмотреть",
    "можно тебя увидеть",
    "дай увидеть тебя",
    "хочу увидеть как ты выглядишь",
    "покажи как ты выглядишь",
    "как ты сейчас выглядишь",
    "как ты выглядишь",
    "как выглядишь",
    "покажи мне себя",
    "покажи мне тебя",
    "покажи свою внешность",
    "покажи свой вид",
    "покажи себя",
    "покажись мне",
    "покажись в кадре",
    "покажись",
    "покажи личико",
    "покажи мордочку",
    "появись",
    "сфоткайся",
    "пришли свое фото",
    "скинь свое фото",
    "хочу твое фото",
    "я тебя не вижу",
)


_PROMPT_REFUSAL = re.compile(
    r"(?i)^(\s*(извин\w*|прости\w*|не могу\w*|не буду\w*|отказ\w*|отказываюсь\w*|не стану\w*|"
    r"не могу помочь|не могу выполнить|наруша\w*|"
    r"sorry|i can't|cannot|refus\w*|decline\w*|i won't|against|policy))",
)
# Минимальный словарь русских слов → английские для fallback-перевода запросов
_RU_EN = {
    r"\bдевушка\b": "girl",
    r"\bдевушк[уи]\b": "girl",
    r"\bженщина\b": "woman",
    r"\bаниме\b": "anime",
    r"\bсидит\b": "sitting",
    r"\bстоит\b": "standing",
    r"\bлежит\b": "lying",
    r"\bидёт\b": "walking",
    r"\bбежит\b": "running",
    r"\bкружевн\w+\b": "lace",
    r"\bбель[её]\b": "lingerie",
    r"\bплат[ьъ]е\b": "dress",
    r"\bчёрн\w+\b": "black",
    r"\bбел\w+\b": "white",
    r"\bкрасн\w+\b": "red",
    r"\bсил\w+\b": "blue",
    r"\bзелён\w+\b": "green",
    r"\bрозов\w+\b": "pink",
    r"\bстол\b": "table",
    r"\bстул\b": "chair",
    r"\bкровать\b": "bed",
    r"\bдиван\b": "couch",
    r"\bдуш\w+\b": "shower",
    r"\bкухн\w+\b": "kitchen",
    r"\bкомната\b": "room",
    r"\bсвет\w+\b": "lighting",
    r"\bтёмн\w+\b": "dark",
    r"\bмягк\w+\b": "soft",
    r"\bярк\w+\b": "bright",
    r"\bстиль\b": "style",
    r"\bспин\w+\b": "back",
    r"\bспиной\b": "back to camera",
    r"\bлицом\b": "facing",
    r"\bв полный рост\b": "full body",
    r"\bкрупн\w+ план\b": "close-up",
    r"\bна стул\w*\b": "on a chair",
    r"\bна полу\b": "on the floor",
    r"\bсзади\b": "from behind",
}

def _translate_scene(text: str) -> str:
    """Минимальный перевод русских слов в запросе на английские эквиваленты."""
    if not text:
        return text
    result = text
    for pattern, replacement in _RU_EN.items():
        result = re.sub(pattern, replacement, result, flags=re.I)
    return result.strip()

def _extract_json_prompt(text: str) -> str:
    """Пытается извлечь prompt из JSON-объекта внутри text."""
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        try:
            data = json.loads(text[i:j + 1])
            if isinstance(data, dict):
                for key in ("prompt", "rewritten_prompt", "t2i", "qwen", "edit", "output"):
                    v = str(data.get(key) or "").strip()
                    if v and v.lower() not in ("none", "null"):
                        return v
        except Exception:
            pass
    elif i >= 0 and j < 0:
        # Обрезанный JSON (упёрлись в max_tokens) — не парсим
        return None
    return ""


def _clean_prompt(prompt: str) -> str:
    """Удаляет лишние кавычки, markdown, префиксы."""
    s = (prompt or "").strip()
    if not s:
        return s
    # markdown-блоки
    if "```" in s:
        m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", s, re.DOTALL)
        if m and m.group(1).strip():
            s = m.group(1).strip()
    # JSON-префикс
    if s.lower().startswith("json"):
        s = s[4:].strip()
    # Внешние кавычки
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    # Префиксы-согласия
    s = re.sub(
        r"(?i)^(конечно[,.!]?\s*(вот\s+)?)?(вот\s+)?(промт?|prompt)"
        r"[\"']?\s*[:\-]\s*",
        "",
        s,
    ).strip()
    s = s.strip(" \n\"'`„“«»")
    if _PROMPT_REFUSAL.search(s):
        return ""
    return s


def extract_prompt_from_llm(raw_response, user_request: str = "") -> str:
    """Извлекает промт из ответа LLM. Многоуровневый парсинг:

    1. content (из choices[0].message.content) → JSON → очистка.
    2. reasoning_content → JSON → очистка.
    3. tool_calls → JSON → очистка.
    4. JSON внутри сырого ответа.
    5. Regex "prompt": "..." в тексте.
    6. Текст как есть (очищенный).

    Принимает dict (полный ответ API) или готовую строку.
    """
    # ── Уровень 0: извлечь сырой текст из ответа API ─────────────────────
    choices_list = None
    message = {}

    if isinstance(raw_response, dict):
        choices_list = raw_response.get("choices") or []
        if choices_list:
            chunk = choices_list[0] or {}
            fr = chunk.get("finish_reason") or ""
            if fr in ("content_filter",):
                return ""
            message = chunk.get("message") or {}
            if "refusal" in message:
                return ""
        else:
            # Нет choices — плоский dict вида {"message": {"content": "..."}} или {"content": "...", "reasoning_content": "..."}
            message = raw_response.get("message") or raw_response

        # 1. content
        content = str(message.get("content") or "").strip()
        if content:
            jv = _extract_json_prompt(content)
            if jv:
                cleaned = _clean_prompt(jv)
                if len(cleaned) >= 3:
                    return cleaned
            if jv is None:
                # Обрезанный JSON — не чистим как текст, падаем в fallback
                content = ""
            else:
                # Если JSON не дал — полная очистка
                cleaned = _clean_prompt(content)
                if len(cleaned) >= 3:
                    return cleaned

        # 2. reasoning_content
        reasoning = str(message.get("reasoning_content") or "").strip()
        if reasoning:
            jv = _extract_json_prompt(reasoning)
            if jv:
                cleaned = _clean_prompt(jv)
                if len(cleaned) >= 3:
                    return cleaned
            if jv is None:
                reasoning = ""
            else:
                cleaned = _clean_prompt(reasoning)
                if len(cleaned) >= 3:
                    return cleaned

        # 3. tool_calls
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = str(fn.get("arguments") or "").strip()
            if args:
                jv = _extract_json_prompt(args)
                if jv:
                    cleaned = _clean_prompt(jv)
                    if len(cleaned) >= 3:
                        return cleaned
                if jv is None:
                    args = ""
                else:
                    cleaned = _clean_prompt(args)
                    if len(cleaned) >= 3:
                        return cleaned

        # 4. Весь сырой ответ (без choices) — если message пуст, пробуем raw_response целиком
        raw = str(raw_response.get("content") or raw_response.get("message", {}).get("content") or "").strip() if not choices_list else ""
    else:
        raw = str(raw_response or "").strip()

    if choices_list and not raw:
        # Если choices есть, но content/reasoning/tool_calls не дали результат,
        # пробуем chunk целиком (только content, не finish_reason!)
        chunk = choices_list[0] or {}
        raw = str(chunk.get("content") or "").strip()

    if not raw:
        return ""

    text = raw

    # ── Уровень 5: JSON-объект внутри текста ──────────────────────────────
    prompt = _extract_json_prompt(text)
    if prompt is None:  # обрезанный JSON
        return ""

    # ── Уровень 6: Regex "prompt": "..." ───────────────────────────────────
    if not prompt:
        m = re.search(r"[\"']prompt[\"']\s*[:=]\s*[\"']([^\"']+)[\"']", text)
        if m:
            prompt = m.group(1).strip()

    # ── Уровень 7: текст как есть (очищенный) ─────────────────────────────
    if not prompt:
        cleaned = _clean_prompt(raw)
        if cleaned:
            prompt = cleaned

    prompt = (prompt or "").strip()
    if len(prompt) < 3:
        return ""
    return prompt
extract_prompt_from_response = extract_prompt_from_llm  # alias for compatibility


class PluginImpl(Plugin):
    id = "phone_media"
    name = "Генератор картинок"
    version = "6.3.1"
    description = "Qwen-Image 2.1: текст, правка референса, «то же самое, но X»"
    settings_tab = "own"
    settings_tab_title = "Генератор"
    settings_schema = [
        SettingField("enabled", "Включить", "bool", True),
        SettingField("comfy_url", "ComfyUI", "str", "http://127.0.0.1:8188"),
        SettingField("timeout_sec", "Таймаут сек", "int", 900, min_value=60, max_value=900),
        SettingField("steps", "Steps (если есть KSampler)", "int", 8, min_value=4, max_value=60),
        SettingField("width", "Ширина latent", "int", 1088, min_value=512, max_value=2048),
        SettingField("height", "Высота latent", "int", 1808, min_value=512, max_value=2048),
        SettingField("positive_prefix", "Префикс промпта", "str", "masterpiece, best quality, anime"),
        SettingField("default_workflow", "Workflow по умолчанию", "str", "qwen"),
        SettingField("ask_workflow", "Спрашивать движок каждый раз", "bool", True),
    ]

    def __init__(self):
        self.app = None
        self._ui: Dict[str, Any] = {}
        self._busy = False

    def on_load(self, app: AppContext) -> None:
        self.app = app
        app.state.setdefault("imggen_stage", "idle")
        print("🖼 imggen 6.3.1: show yourself / t2i / edit / continuation (то же самое) + fallback + diagnostics", flush=True)
        self._llm_probe(app)

    def register_tools(self, app: AppContext) -> None:
        app.tools["generate_image"] = self.tool_generate

    def setup_settings_tab(self, tab, app: AppContext) -> bool:
        from PyQt5 import QtWidgets
        lay = QtWidgets.QVBoxLayout(tab)
        en = QtWidgets.QCheckBox("Включить генератор")
        en.setChecked(bool(app.get_plugin_setting(self.id, "enabled", True)))
        ask = QtWidgets.QCheckBox("Если приложено фото и неясно — спросить: текст или правка")
        ask.setChecked(bool(app.get_plugin_setting(self.id, "ask_workflow", True)))
        lay.addWidget(en)
        lay.addWidget(ask)
        url = QtWidgets.QLineEdit(str(app.get_plugin_setting(self.id, "comfy_url", "http://127.0.0.1:8188")))
        pref = QtWidgets.QLineEdit(str(app.get_plugin_setting(self.id, "positive_prefix", "")))
        dflt = QtWidgets.QComboBox()
        dflt.setEditable(True)
        for n in ("t2i", "edit"):
            dflt.addItem(n)
        dflt.setEditText(str(app.get_plugin_setting(self.id, "default_workflow", "t2i")))
        tout = QtWidgets.QSpinBox(); tout.setRange(60, 900)
        tout.setValue(int(app.get_plugin_setting(self.id, "timeout_sec", 900) or 900))
        form = QtWidgets.QFormLayout()
        form.addRow("ComfyUI", url)
        form.addRow("По умолчанию", dflt)
        form.addRow("Ждать сек", tout)
        lay.addLayout(form)
        lay.addWidget(QtWidgets.QLabel(
            "Графы:\n"
            "qwen21_t2i.json — картинка по тексту\n"
            "qwen21_edit.json — правка прикреплённого фото\n"
            "qwen21_i2i.json — «то же самое» по последнему кадру"
        ))
        self._ui = dict(enabled=en, ask_workflow=ask, comfy_url=url, positive_prefix=pref,
                        default_workflow=dflt, timeout_sec=tout)
        lay.addStretch(1)
        return True

    def collect_settings_tab(self) -> Dict[str, Any]:
        u = self._ui
        return {
            "enabled": u["enabled"].isChecked(),
            "ask_workflow": u["ask_workflow"].isChecked(),
            "comfy_url": u["comfy_url"].text().strip(),
            "positive_prefix": u["positive_prefix"].text().strip(),
            "default_workflow": u["default_workflow"].currentText().strip() or "t2i",
            "timeout_sec": int(u["timeout_sec"].value()),
        }

    def on_user_message(self, text, app):
        if not app.get_plugin_setting(self.id, "enabled", True):
            return None
        low = (text or "").strip().lower().replace("ё", "е")
        low = low.replace("этоты", "это ты").replace("это-ты", "это ты")
        if not low:
            return None
        stage = str(app.state.get("imggen_stage") or "idle")
        age = time.time() - float(app.state.get("imggen_at") or 0)
        if stage != "idle" and age > 600:
            app.state["imggen_stage"] = "idle"
            stage = "idle"

        if self._wants_show_ref(low):
            return HookResult(True, self._show_character_ref(app))
        seen = self._see_self_rest(low)
        if seen is not None:
            if len(seen) < 3:
                if self._character_ref_img(app):
                    return HookResult(True, self._show_character_ref(app, seeing=True))
                return HookResult(True, self._after_scene(app, "нарисуй себя"))
            return HookResult(True, self._after_scene(app, "нарисуй себя " + seen))
        if self._is_self_remember(low):
            return HookResult(True, self._save_character_ref(app))
        if self._is_search_not_draw(low):
            return None

        if stage == "drafting" and age < 25:
            if self._has_word(low, "отмена", "стоп") or "не надо" in low:
                app.state["imggen_stage"] = "idle"
                app.state["imggen_job"] = 0
                return HookResult(True, "ок, остановила сборку промпта.")
            return HookResult(True, "уже собираю один промпт, не пересобираю.")
        if stage == "await_model":
            if self._has_word(low, "отмена", "стоп") or "не надо" in low:
                app.state["imggen_stage"] = "idle"
                return HookResult(True, "ок, не рисую.")
            choice = low.strip(" .!")
            fam = ""
            if choice in ("1", "текст", "t2i", "новая") or choice.startswith("1 ") or choice.startswith("1."):
                fam = "t2i"
            elif choice in ("2", "правка", "edit", "референс") or choice.startswith("2 ") or choice.startswith("2.") or "правк" in choice:
                fam = "edit"
            else:
                picked = self._match_choice(low, self._choices())
                if picked is not None and picked.exists():
                    fam = self._family(picked.stem)
            if not fam:
                return HookResult(True, "напиши 1 (только текст) или 2 (правка фото).")
            req = str(app.state.get("imggen_request") or text)
            refs_now = self._refs(app) or list(app.state.get("imggen_refs") or [])
            if fam == "edit" and not refs_now:
                return HookResult(True, "для правки сначала прикрепи фото.")
            self._begin_one(app, req, wf=fam)
            label = "правка" if fam == "edit" else "текст"
            return HookResult(True, f"{label}. один промпт и сразу в генерацию.")
        if stage == "await_prompt":
            if self._has_word(low, "отмена", "стоп") or "не надо" in low:
                app.state["imggen_stage"] = "idle"
                return HookResult(True, "ок, не рисую.")
            return HookResult(True, self._after_scene(app, text))
        if stage == "confirm" and not (self._is_prompt_revise(low) and self._has_recent_gen(app)):
            return self._handle_confirm(app, text, low)
        if stage == "busy":
            if self._has_word(low, "отмена", "стоп") or "стоп генерац" in low or "не надо картин" in low:
                app.state["imggen_stage"] = "idle"
                app.state["imggen_job"] = 0
                return HookResult(True, "ок, не жду эту картинку. можно просить новую.")
            if not self._is_prompt_revise(low) and not any(k in low for k in _ASK):
                return None

        if self._is_continuation_request(low) and self._cont_source(app):
            if stage in ("drafting", "confirm", "busy"):
                return HookResult(True, "подожди: сначала догенерю текущую картинку, потом сделаю «то же самое».")
            return HookResult(True, self._continuation(app, text))

        if self._is_prompt_revise(low) and self._has_recent_gen(app):
            return HookResult(True, self._revise(app, text))

        if any(k in low for k in _ASK):
            scene = self._user_scene(text)
            wf = self._pick_workflow_name(low)
            if wf:
                app.state["imggen_workflow_hint"] = wf
            if len(scene) < 8:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_request"] = text  # сохранить исходный запрос для контекста персонажа
                app.state["imggen_at"] = time.time()
                if wf:
                    app.state["imggen_workflow_hint"] = wf
                return HookResult(True, "что рисуем? опиши сцену. фото нужно только если это правка.")
            return HookResult(True, self._after_scene(app, text))
        return None

    @staticmethod
    def _has_word(text: str, *words: str) -> bool:
        t = text or ""
        for w in words:
            if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", t, flags=re.I):
                return True
        return False

    def _begin_one(self, app, raw: str, wf: str = "", refs: Optional[List[str]] = None, kind: str = "") -> None:
        """Один промпт от модели и сразу очередь ComfyUI. Без второй сборки."""
        low = (raw or "").lower()
        if not wf:
            wf = self._pick_workflow_name(low) or str(app.state.get("imggen_workflow_hint") or "")
        wf = "edit" if self._family(wf) == "edit" else "t2i"
        if "\nCHANGE:\n" not in (raw or ""):
            app.state["imggen_origin"] = raw
        if refs is None:
            if wf == "edit":
                refs = self._refs(app) or list(app.state.get("imggen_refs") or [])
            else:
                refs = self._attached(app)
        name, _look = self._char_look(app)
        about = self._about_character(f"{raw}\n{app.state.get('imggen_request') or ''}", name)
        if about and not self._wants_fresh(low) and not self._wants_plain(low) and kind not in ("edit", "plain", "cont"):
            char_ref = self._character_ref_img(app)
            if char_ref and not (self._wants_edit(low) and self._attached(app) and kind != "self"):
                # Не затирать screen-ref (пришёл из _after_scene с source=last)
                has_screen_ref = bool(refs) and any(
                    Path(str(r)).stem.startswith(("pick_", "gen_")) for r in refs
                )
                if not has_screen_ref:
                    wf = "edit"
                    refs = [char_ref]
                    kind = "self"
        print(f"imggen: route wf={wf} kind={kind or '-'} about={about} refs={list(refs or [])[:1]}", flush=True)
        job = time.time()
        app.state["imggen_request"] = raw
        app.state["imggen_refs"] = list(refs or [])
        app.state["imggen_ref_kind"] = kind or ("edit" if wf == "edit" else "")
        app.state["imggen_stage"] = "drafting"
        app.state["imggen_at"] = time.time()
        app.state["imggen_workflow_hint"] = ""
        app.state["imggen_last_wf"] = wf
        app.state["imggen_job"] = job
        app.state["imggen_kind"] = kind
        if kind != "cont":
            app.state.pop("imggen_fixed_seed", None)
        self._schedule_one(app, raw, refs, wf, job)

    def _choices(self) -> List[Path]:
        folder = Path(__file__).resolve().parent / "workflows"
        out: List[Path] = []
        if not folder.is_dir():
            return out
        for p in sorted(folder.glob("*.json")):
            try:
                if self._is_stub_wf(p):
                    continue
            except Exception:
                continue
            out.append(p)
        return out

    def _default_path(self, paths: List[Path]) -> Optional[Path]:
        if not paths:
            return None
        want = str(self.app.get_plugin_setting(self.id, "default_workflow", "") or "").strip().lower() if self.app else ""
        for p in paths:
            if want and want in (p.stem.lower(), self._family(p.stem)):
                return p
        return paths[0]

    def _model_menu(self, paths: List[Path]) -> str:
        if not paths:
            return "в папке workflows/ нет живых графов."
        lines = ["Каким графом рисовать? Напиши номер или имя файла:"]
        for i, p in enumerate(paths, 1):
            lines.append(f"{i}. {p.stem}")
        return "\n".join(lines)

    def _match_choice(self, low: str, paths: List[Path]) -> Optional[Path]:
        text = (low or "").strip().lower()
        if not text or not paths:
            return None
        m = re.match(r"^(\d+)\b", text)
        if m:
            i = int(m.group(1))
            if 1 <= i <= len(paths):
                return paths[i - 1]
        named = self._pick_workflow_name(text)
        if named:
            resolved = self._resolve_wf(named)
            if resolved.exists() and not self._is_stub_wf(resolved):
                return resolved
        for p in paths:
            stem = p.stem.lower()
            if stem == text or stem in text or stem.replace("_", " ") in text:
                return p
        return None

    def _wants_edit(self, low: str) -> bool:
        low = low or ""
        if any(k in low for k in ("промпт", "промт")):
            return False
        return self._wants_ref(low)

    def _wants_ref(self, low: str) -> bool:
        return any(k in (low or "") for k in _EDIT_WORDS)

    def _wants_plain(self, low: str) -> bool:
        return self._wants_fresh(low) or any(k in (low or "") for k in _PLAIN_WORDS)

    @staticmethod
    def _is_search_not_draw(low: str) -> bool:
        find = any(w in low for w in ("найди", "поищи", "погугли", "загугли", "пришли", "скинь", "закинь"))
        if not find:
            return False
        draw = any(w in low for w in ("нарисуй", "нарисовать", "сгенери", "изобрази", "draw "))
        return not draw

    def _is_refer_to_last(self, low: str, app) -> bool:
        """Пользователь ссылается на последнее изображение (screen pick / gen)."""
        if not low:
            return False
        # явные отсылки к «этому» как к объекту, а не части речи
        refer = any(w in low for w in (
            "нарисуй это", "нарисуй то же", "нарисуй такое",
            "сделай такое", "сделай такую", 
            "похож", "подобн",
            "на основе этого", "из этого",
            "как на экран", "с экрана", "с монитора",
            "то же", "такое же", "такой же",
        ))
        if not refer:
            return False
        # проверяем свежесть screen pick или генерации
        screen_at = float(app.state.get("screen_last_at") or 0)
        gen_at = float(app.state.get("imggen_done_at") or 0)
        last_img = app.state.get("phone_media_last")
        if not last_img or not Path(str(last_img)).is_file():
            return False
        now = time.time()
        recent_screen = screen_at and (now - screen_at) < 120       # 2 минуты
        recent_gen = gen_at and (now - gen_at) < 300                  # 5 минут
        return bool(recent_screen or recent_gen)

    def _is_prompt_revise(self, low: str) -> bool:
        if any(w in low for w in ("напоминан", "заметк", "календар", "задач", "список", "файл", "окно", "вкладк", "громч", "тише")):
            return False
        if any(k in low for k in ("промпт", "промт")) and any(
            w in low for w in ("измени", "поправь", "перепиши", "добавь", "убери", "другой", "переделай")
        ):
            return True
        return any(w in low for w in (
            "добавь", "убери", "заново", "ещё раз", "еще раз", "перегенерир",
            "другой вариант", "тот же", "потемнее", "посветлее", "крупнее",
        ))

    def _has_recent_gen(self, app) -> bool:
        if not (app.state.get("imggen_last_prompt") or app.state.get("imggen_request")):
            return False
        last = float(app.state.get("imggen_done_at") or app.state.get("imggen_at") or 0)
        return bool(last) and time.time() - last < 1800

    def _revise(self, app, text: str) -> str:
        prev = str(app.state.get("imggen_last_prompt") or "")
        base = str(app.state.get("imggen_request") or "")
        low = (text or "").lower()
        refs = self._refs(app) or list(app.state.get("imggen_refs") or [])
        kind = ""
        if any(k in low for k in ("промпт", "промт")) or self._wants_plain(low):
            wf = "t2i"
            refs = []
            kind = "plain"
        elif self._wants_ref(low) or str(app.state.get("imggen_last_wf") or "") == "edit":
            wf = "edit"
        else:
            wf = "t2i"
        if wf == "edit" and not refs:
            name, _ = self._char_look(app)
            char_ref = ""
            if self._about_character(text, name):
                char_ref = self._character_ref_img(app)
            if char_ref:
                refs = [char_ref]
                kind = "self"
            else:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_workflow_hint"] = "edit"
                app.state["imggen_request"] = (text or "").strip()
                app.state["imggen_at"] = time.time()
                return "для правки фото нужен кадр. прикрепи его и повтори, что изменить."
        origin = str(app.state.get("imggen_origin") or base)
        merged = (
            "REVISION\n"
            f"ORIGIN:\n{origin[:800]}\n"
            f"PREVIOUS:\n{prev[:1800]}\n"
            "CHANGE:\n"
            f"{text}"
        )
        self._begin_one(app, merged, wf=wf, refs=refs if wf == "edit" else None, kind=kind)
        app.state["imggen_request"] = (text or "").strip()
        return "обновляю промпт и сразу отправляю в ComfyUI."

    def _wants_fresh(self, low: str) -> bool:
        return any(k in (low or "") for k in ("с нуля", "новую картин", "по тексту", "без референс", "без фото", "только текст"))

    # ── «то же самое, но X» — продолжение последней генерации ─────────

    def _history(self, app):
        h = getattr(app, "_gen_history", None)
        if h is None:
            try:
                from core.generation_history import get_history
                h = get_history()
            except Exception:
                h = None
        return h

    @staticmethod
    def _is_continuation_request(low: str) -> bool:
        try:
            from core.continuation_detector import is_continuation
            return bool(is_continuation(low))
        except Exception:
            return False

    def _cont_settings(self, app) -> dict:
        stored = app.get_plugin_setting(self.id, "continuation", {}) or {}
        cfg = dict(_CONT_DEFAULTS)
        if isinstance(stored, dict):
            cfg.update(stored)
        return cfg

    def _cont_log(self, app, msg: str) -> None:
        try:
            log_dir = Path(__file__).resolve().parents[2] / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_dir / "imggen.log", "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().isoformat(timespec='seconds')}] [continuation] {msg}\n")
        except Exception:
            pass

    def _cont_source(self, app):
        """Последняя запись истории с существующим файлом картинки."""
        h = self._history(app)
        if h is None:
            return None
        try:
            rec = h.last()
        except Exception:
            return None
        if not rec or not rec.output_path or not Path(str(rec.output_path)).is_file():
            return None
        return rec

    def _cont_run_wf(self, app) -> str:
        """Какой workflow взять под «то же самое»: из настроек, потом edit, потом t2i."""
        wf_cfg = app.get_plugin_setting(self.id, "workflows", {}) or {}
        cand = str(wf_cfg.get("i2i") or "i2i")
        names = [cand]
        if cand.lower().endswith(".json"):
            names.append(Path(cand).stem)
        for name in names + ["edit"]:
            p = self._resolve_wf(name)
            if p.exists() and not self._is_stub_wf(p):
                return name
        return "t2i"

    def _cont_plan(self, app, text: str) -> Optional[dict]:
        """План продолжения: refs=[последний кадр], seed=старый, промт=REVISION."""
        cfg = self._cont_settings(app)
        if not cfg.get("enabled", True):
            return None
        if not self._is_continuation_request(text):
            return None
        rec = self._cont_source(app)
        if not rec or not cfg.get("use_last_as_reference", True):
            return None
        refs = [str(rec.output_path)]
        seed = rec.seed if cfg.get("preserve_seed", True) else None
        wf = self._cont_run_wf(app)
        prev = str(rec.prompt or "")
        merged = (
            "REVISION\n"
            f"ORIGIN:\n{prev[:800]}\n"
            f"PREVIOUS:\n{prev[:1800]}\n"
            "CHANGE:\n"
            f"{text}"
        )
        return {"raw": merged, "refs": refs, "wf": wf, "seed": seed, "record": rec}

    def _continuation(self, app, text: str) -> str:
        plan = self._cont_plan(app, text)
        if not plan:
            return self._after_scene(app, text)
        rec = plan["record"]
        try:
            trig = continuation_trigger(text) or "..."
        except Exception:
            trig = "..."
        prev_name = Path(rec.output_path).name
        print(
            f"imggen: continuation detected trigger={trig!r} "
            f"prev={prev_name} wf={plan['wf']} seed={plan['seed']}",
            flush=True,
        )
        self._cont_log(
            app,
            f"detected: yes (trigger={trig!r}); previous: {prev_name}, "
            f"prompt_len={len(rec.prompt or '')}; refs=[{prev_name}], "
            f"wf={plan['wf']}, seed={plan['seed']}",
        )
        if plan["seed"]:
            app.state["imggen_fixed_seed"] = plan["seed"]
        self._begin_one(app, plan["raw"], wf=plan["wf"], refs=plan["refs"], kind="cont")
        app.state["imggen_request"] = (text or "").strip()
        return (
            "это продолжение прошлого кадра: беру его как референс, "
            "сохраняю все детали и меняю только то, что попросил."
        )

    def _after_scene(self, app, raw: str) -> str:
        low = (raw or "").lower().replace("ё", "е")
        named = self._pick_workflow_name(low)
        plain = self._wants_plain(low)
        if not plain and self._is_continuation_request(low) and self._cont_source(app):
            return self._continuation(app, raw)
        ref = self._wants_ref(low) or self._is_refer_to_last(low, app)
        if named:
            ref = self._family(named) == "edit"
            plain = not ref
        pending = self._pending(app)
        name, _ = self._char_look(app)
        stored = str(app.state.get("imggen_request") or "")
        about = self._about_character(raw, name) or (stored and self._about_character(stored, name))
        kind = ""
        refs: List[str] = []
        edit = False
        if plain:
            edit = False
            kind = "plain"
            print("imggen: decide plain t2i", flush=True)
        elif ref:
            edit = True
            refs = pending or self._recent_image(app)
            if not refs and about:
                char_ref = self._character_ref_img(app)
                if char_ref:
                    refs = [char_ref]
                    kind = "self"
            print(f"imggen: decide reference refs={bool(refs)}", flush=True)
        elif about:
            char_ref = self._character_ref_img(app)
            if char_ref:
                edit = True
                refs = [char_ref]
                kind = "self"
                print("imggen: decide self ref", flush=True)
            else:
                print("imggen: decide character but no ref, t2i", flush=True)
        elif pending and bool(app.get_plugin_setting(self.id, "ask_workflow", True)) and not named:
            app.state["imggen_stage"] = "await_model"
            app.state["imggen_request"] = raw
            app.state["imggen_refs"] = pending
            app.state["imggen_at"] = time.time()
            return (
                "к сообщению приложено фото. что делаем?\n"
                "1. новая картинка только по тексту\n"
                "2. правка этого фото"
            )
        else:
            print("imggen: decide t2i", flush=True)
        if edit and not refs:
            app.state["imggen_stage"] = "await_prompt"
            app.state["imggen_workflow_hint"] = "edit"
            app.state["imggen_request"] = raw
            app.state["imggen_at"] = time.time()
            return "для правки прикрепи фото или скажи, что рисовать с нуля."
        wf = "edit" if edit else "t2i"
        self._begin_one(app, raw, wf=wf, refs=refs if edit else [], kind=kind or ("plain" if not edit else ""))
        if edit:
            if kind == "self":
                return "рисую себя по сохранённому фото. один промпт и сразу в генерацию."
            return "правка референса. один промпт-инструкция и сразу в Qwen-Image 2.1."
        return "картинка по тексту, без референса. один промпт и сразу рисую."

    def _schedule_one(self, app, text, refs, wf: str, job: float = 0):
        async def run():
            nonlocal refs  # иначе refs = [] ниже делает refs локальной для всей run()
            try:
                prompt = await self._one_prompt(app, text, refs, wf)
            except Exception as e:
                print(f"imggen one prompt: {e}", flush=True)
                prompt = ""
            if job and app.state.get("imggen_job") != job:
                return
            if app.state.get("imggen_stage") not in ("drafting", "confirm"):
                return
            if not prompt:
                app.state["imggen_stage"] = "idle"
                raw = str(app.state.get("imggen_last_raw") or "")[:300]
                self._notify(app, self._llm_fail_message(app, raw), None)
                return
            if app.state.get("imggen_llm_fallback"):
                self._notify(
                    app,
                    "⚠ LLM не вернула промт (пустой ответ). Использую упрощённый промт. "
                    "Проверь ProxyAPI, если результат не подходит.",
                    None,
                )
            path = self._resolve_wf(wf)
            fam = self._family(wf or path.stem)
            app.state["imggen_prompt"] = prompt
            app.state["imggen_last_prompt"] = prompt
            app.state["imggen_prompt_" + fam] = prompt
            app.state["imggen_last_wf"] = fam
            is_cont = str(app.state.get("imggen_kind") or "") == "cont"
            if is_cont and fam == "edit" and (not path.exists() or self._is_stub_wf(path)):
                print("imggen: no i2i workflow, fallback to t2i with anchored prompt", flush=True)
                self._cont_log(app, "no i2i workflow, fallback to t2i with anchored prompt")
                fam = "t2i"
                path = self._resolve_wf("t2i")
                refs = []
                app.state["imggen_refs"] = []
                app.state["imggen_ref_kind"] = ""
            if is_cont:
                seed_v = app.state.get("imggen_last_seed") or app.state.get("imggen_fixed_seed")
                self._cont_log(
                    app,
                    f"new prompt_len={len(prompt)}; refs={[Path(r).name for r in refs]}; "
                    f"wf={fam}; seed={seed_v}",
                )
            if not path.exists() or self._is_stub_wf(path):
                app.state["imggen_stage"] = "confirm"
                self._notify(app, f"промпт есть, но нет графа {path.name}.", None)
                return
            if fam == "edit" and not refs:
                fam = "t2i"
                path = self._resolve_wf("t2i")
                app.state["imggen_last_wf"] = fam
            app.state["imggen_stage"] = "busy"
            title = "рисую себя по сохранённому фото" if app.state.get("imggen_ref_kind") == "self" else (
                "правка референса" if fam == "edit" else "картинка по тексту"
            )
            self._notify(app, f"{title}.\n«{prompt[:700]}»\n\nотправляю в ComfyUI.", None)
            self._start(app, prompt, path, list(refs or []), job)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(run())
                return
        except Exception:
            pass

        def work():
            asyncio.run(run())

        threading.Thread(target=work, name="imggen-one", daemon=True).start()

    async def _one_prompt(self, app, request: str, refs: list, wf: str) -> str:
        refs = list(refs or [])  # defensive: не дать UnboundLocalError в любой ветке
        app.state["imggen_llm_fallback"] = False
        fam = self._family(wf)
        kind = str(app.state.get("imggen_kind") or "")
        cont_cfg = self._cont_settings(app)
        name, look = self._char_look(app)
        origin, previous, change, fresh = self._split_revision(request)
        about = self._about_character(f"{origin}\n{change or fresh}", name)
        if fam == "edit" and kind == "self":
            system = (
                "Ты пишешь финальную инструкцию для Qwen-Image-2.1 Edit. "
                "Граф её больше не переписывает. <image1> — фото внешности персонажа, не готовая сцена. "
                "Сохрани лицо, волосы, уши и узнаваемость человека с <image1>. "
                "Сцену, позу, одежду и фон собери заново по запросу пользователя. "
                "На картинке должна быть девушка, не мужчина. "
                "Английский язык, короткие указания. Обязательно начни с <image1>. "
                "Не пиши masterpiece, 8k, best quality, highly detailed. "
                "Не копируй подписи ORIGIN, PREVIOUS, CHANGE. "
                'Верни ТОЛЬКО JSON без markdown: {"prompt": "<инструкция на английском>"}. '
                "Без пояснений, только JSON."
            )
        elif fam == "edit":
            system = (
                "Ты пишешь финальную инструкцию для Qwen-Image-2.1 Edit. "
                "Граф её больше не переписывает. Это не описание новой картинки. "
                "Английский язык, короткие указания. Исходное фото называй <image1>. "
                "Напиши, что изменить, и что оставить: pose, face, expression, clothes, "
                "background, composition. Оставляй их, если пользователь прямо не просит менять. "
                "Если пользователь просит «то же самое, но X» — это правка последнего кадра: "
                "сохрани ВСЕ детали предыдущего (одежда, фон, свет, стиль, поза) и измени только X. "
                "Композицию подгони под изменение: «спиной» → view from behind, back to camera; "
                "«в полный рост» → full body, full view. "
                "Инструкцию начинай с <image1> и перечисляй, что оставить. "
                "Не пиши masterpiece, 8k, best quality, highly detailed. "
                "Не подменяй человека и не описывай кадр с нуля. "
                "Если это правка старой инструкции, сохрани прежние keep и внеси только CHANGE. "
                "Не копируй подписи ORIGIN, PREVIOUS, CHANGE. "
                'Верни ТОЛЬКО JSON без markdown: {"prompt": "<инструкция на английском>"}. '
                "Без пояснений, только JSON."
            )
        else:
            system = (
                "Ты пишешь финальный промпт для Qwen-Image-2.1 text-to-image. "
                "Граф его больше не переписывает. Один английский абзац, как будто кадр уже перед тобой. "
                "Настоящее время. Нельзя: make sure, create, ensure, you, masterpiece, 8k, "
                "highly detailed, award-winning, best quality, разрешение, соотношение сторон, пиксели. "
                "Первая фраза строго такого вида: "
                "The image is a <vertical|wide|square> <style> <photograph|illustration|poster|scene> of <subject>, <background>. "
                "Дальше где что стоит: left, centre, right, upper third, lower third. "
                "Одежда и материалы отдельно. Отдельное предложение The lighting is.... "
                "Последнее предложение The overall composition.... "
                "Читаемый текст на картинке оставляй в исходной письменности внутри прямых двойных кавычек. "
                "Если даны PREVIOUS и CHANGE — ты продолжаешь предыдущий кадр: "
                "сохрани ВСЕ детали PREVIOUS (персонаж, одежда, материалы, мебель, свет, стиль, композицию), "
                "измени только то, что просит пользователь в CHANGE. Композицию подгони под изменение: "
                "«спиной» → view from behind, back to camera. Промт должен быть таким же полным, как PREVIOUS. "
                "Не копируй подписи ORIGIN, PREVIOUS, CHANGE и не отвечай по-русски. "
                'Верни СТРОГО JSON без markdown: {"prompt": "<твой промт на английском>"}. '
                "Без пояснений, без разметки, только JSON."
            )
        if about:
            subject = (
                f"Персонаж «{name}» — девушка/female. На картинке должна быть девушка, не мужчина.\n"
                f"В кадре персонаж «{name}». Внешность переведи на английский, карточку в ответ не копируй:\n"
                f"{look[:500]}"
            )
        else:
            subject = "Рисуй только запрошенное. Не добавляй персонажа чата и черты, которых нет в запросе."
        if change or previous:
            user = (
                f"{subject}\n"
                f"ORIGIN:\n{origin}\n"
                f"PREVIOUS:\n{previous}\n"
                f"CHANGE:\n{change}"
            )
        else:
            user = f"{subject}\nЗапрос:\n{fresh}"
        # Якоря предыдущего кадра — подставляем в запрос, чтобы LLM их не потеряла.
        anchors = {}
        anchor_block = ""
        if previous and cont_cfg.get("anchor_extraction", True) and extract_anchors:
            anchors = extract_anchors(previous, extra_text=look)
            anchor_block = anchors_text(anchors) if anchors_text else ""
            if anchor_block:
                user += (
                    "\n\n[ЯКОРЯ предыдущего кадра — сохрани их ВСЕ в ответе:]\n"
                    + anchor_block
                )
        raw = await self._llm_complete(app, system, user) or ""
        prompt = extract_prompt_from_llm(raw, request)
        if not prompt:
            app.state["imggen_llm_fallback"] = True
            print(f"imggen llm: пустой ответ raw={raw[:200]!r} → fallback на запрос пользователя", flush=True)
            prompt = self._fallback_prompt(app, request, fam)
        # Анти-дрейф: возвращаем недостающие якоря и, если промт подозрительно
        # короткий (t2i), делаем один строгий retry.
        if previous and prompt:
            if anchor_block and apply_anchors:
                prompt = apply_anchors(prompt, anchors)
            if fam != "edit" and check_prompt_drift:
                drift, stats = check_prompt_drift(
                    prompt,
                    previous,
                    min_length_ratio=0.7,
                    min_similarity=float(cont_cfg.get("min_prompt_similarity", 0.6) or 0.6),
                )
                if drift:
                    app.state["imggen_drift"] = True
                    self._cont_log(app, f"drift detected {stats} → strict retry")
                    print(f"imggen: prompt drift {stats} → strict retry", flush=True)
                    strict_raw = await self._llm_complete(
                        app,
                        _STRICT_CONT_SYSTEM,
                        (
                            f"ПРЕДЫДУЩИЙ ПРОМТ (не короче его, сохрани все детали):\n{previous}\n\n"
                            f"ЯКОРЯ (обязательно вписать):\n{anchor_block or '(ВСЕ детали из PREVIOUS)'}\n\n"
                            f"ИЗМЕНЕНИЕ (только это и поменяй):\n{change or fresh}"
                        ),
                    ) or ""
                    strict_prompt = extract_prompt_from_llm(strict_raw, request)
                    if strict_prompt:
                        prompt = apply_anchors(strict_prompt, anchors) if apply_anchors else strict_prompt
        limit = 900 if fam == "edit" else 3200
        return prompt[:limit]

    def _fallback_prompt(self, app, request: str, family: str = "") -> str:
        """Если LLM не вернула промт — собираем из запроса пользователя + качества.

        Гарантирует, что ComfyUI всегда получит положительный промт.
        """
        base = self._draft_prompt(app, request, family)
        prefix = str(app.get_plugin_setting(self.id, "positive_prefix", "") or "").strip()
        if prefix and prefix not in base:
            base = f"{prefix}, {base}"
        return base

    @staticmethod
    def _split_revision(request: str):
        text = request or ""
        if not text.startswith("REVISION\n"):
            return text, "", "", text
        origin = text.split("ORIGIN:\n", 1)[-1].split("\nPREVIOUS:\n", 1)[0].strip()
        previous = ""
        change = ""
        if "\nPREVIOUS:\n" in text:
            previous = text.split("\nPREVIOUS:\n", 1)[1].split("\nCHANGE:\n", 1)[0].strip()
        if "\nCHANGE:\n" in text:
            change = text.split("\nCHANGE:\n", 1)[1].strip()
        return origin, previous, change, change or origin

    @staticmethod
    def _unwrap_prompt(raw: str) -> str:
        s = (raw or "").strip()
        if s.startswith("```"):
            parts = s.split("```")
            s = parts[1] if len(parts) > 1 else s.strip("`")
            if s.lower().startswith("json"):
                s = s[4:]
            s = s.strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
        if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
            s = s[1:-1].strip()
        return s

    def _schedule_card(self, app, text, refs):
        async def run():
            try:
                card = await self._llm_prompts_then_card(app, text, refs)
            except Exception as e:
                print(f"imggen llm task: {e}", flush=True)
                card = f"не собрала промпт: {e}"
                app.state["imggen_stage"] = "idle"
            else:
                if app.state.get("imggen_stage") == "drafting":
                    app.state["imggen_stage"] = "confirm"
                    app.state["imggen_at"] = time.time()
            self._notify(app, card, None)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(run())
                return
        except Exception:
            pass

        def work():
            asyncio.run(run())

        threading.Thread(target=work, name="imggen-llm", daemon=True).start()

    def _handle_confirm(self, app, text, low):
        if self._has_word(low, "нет") or self._has_word(low, "не то", "отмена"):
            app.state["imggen_stage"] = "idle"
            return HookResult(True, "ок, без картинки. скажи заново, что нарисовать.")
        if any(w in low for w in ("другой промпт", "переделай промпт", "не подойд")):
            prev = str(app.state.get("imggen_last_wf") or "")
            self._begin_one(app, text, wf=prev)
            return HookResult(True, f"тот же граф ({prev or 'по умолчанию'}), промпт один раз.")
        wf = self._pick_workflow_name(low)
        yes = self._has_word(low, "да", "давай", "ок", "го", "пойдёт", "пойдет", "норм", "утвержд", "сгенерируй")
        if wf or yes:
            if not wf:
                wf = str(app.get_plugin_setting(self.id, "default_workflow", "qwen") or "qwen")
            path = self._resolve_wf(wf)
            if not path.exists():
                return HookResult(True, f"нет файла {path.name}. Нужны qwen21_t2i.json или qwen21_edit.json.")
            if self._is_stub_wf(path):
                return HookResult(True,
                    f"{path.name} — не граф ComfyUI. Нужны qwen21_t2i.json и qwen21_edit.json.")
            app.state["imggen_stage"] = "busy"
            fam = self._family(wf or path.stem)
            refs = list(app.state.get("imggen_refs") or [])
            if fam == "edit" and not refs:
                app.state["imggen_stage"] = "confirm"
                return HookResult(True, "для правки нужен референс. прикрепи фото и снова напиши, что изменить.")
            prompt = str(app.state.get("imggen_prompt_" + fam) or app.state.get("imggen_prompt") or "")
            if not prompt:
                self._begin_one(app, str(app.state.get("imggen_request") or text))
                return HookResult(True, "промпта ещё нет. собираю один раз и сразу рисую.")
            self._start(app, prompt, path, refs)
            extra = f", референс: {Path(refs[0]).name}" if refs else ""
            return HookResult(True, f"запустила {path.stem}{extra}. промпт уже готов, второй раз не собираю.")
        scene = self._user_scene(text)
        if len(scene) >= 8:
            return HookResult(True, self._after_scene(app, text))
        paths = self._choices()
        return HookResult(True, self._model_menu(paths) if paths else "напиши имя графа из workflows/.")

    def _ask_card(self, app, prompt: str, refs: List[str]) -> str:
        if app.state.get("imggen_prompt_t2i") or app.state.get("imggen_prompt"):
            return self._ask_card_ready(app, refs)
        req = str(app.state.get("imggen_request") or "")
        t2i = self._draft_prompt(app, req, "t2i")
        edit = self._draft_prompt(app, req, "edit") if refs else ""
        app.state["imggen_prompt_t2i"] = t2i
        app.state["imggen_prompt_edit"] = edit
        app.state["imggen_prompt"] = t2i
        return self._ask_card_ready(app, refs)

    def _pick_workflow_name(self, low: str) -> str:
        # longer keys first
        for k in sorted(_ALIASES, key=len, reverse=True):
            if k in low.split() or f" {k} " in f" {low} " or low.endswith(k) or low.startswith(k):
                return k
        return ""

    def _list_wf(self) -> List[Path]:
        base = Path(__file__).resolve().parent
        out = []
        for folder in (base / "workflows", base):
            if folder.is_dir():
                out.extend(sorted(folder.glob("*.json")))
        return out

    def _resolve_wf(self, name: str) -> Path:
        base = Path(__file__).resolve().parent
        key = (name or "").strip().lower()
        rel = _ALIASES.get(key, key)
        p = Path(rel)
        if not p.is_absolute():
            p = base / rel
        if p.exists():
            return p
        for f in self._list_wf():
            if f.stem.lower() in (key, key.replace(" ", "_")):
                return f
        return p

    def _family(self, name: str) -> str:
        n = (name or "").lower()
        if any(k in n for k in ("edit", "правк", "референс", "i2i", "ipadapter", "img2img")):
            return "edit"
        return "t2i"


    def _about_character(self, request: str, name: str = "") -> bool:
        """Сцена с персонажем чата: имя, «себя», «ассистент», «персонаж»."""
        low = (request or "").lower().replace("ё", "е")
        tokens = []
        cid = (name or "").strip().lower()
        if cid:
            tokens.extend({cid, cid.rstrip("аяуюи")})
        tokens.extend(("лисичка", "лисичку", "лисичке", "лисички", "мила", "милу", "миле", "милы"))
        if any(t and len(t) >= 3 and t in low for t in tokens):
            return True
        if any(w in low for w in (
            "ассистент", "персонаж", "свой образ", "своем образе",
            "моя внешность", "твоя внешность", "этот персонаж", "эту героин",
        )):
            return True
        if re.search(r"(нарисуй|сгенерируй|сделай картин|сделай изображ|изобрази)\s+(себя|тебя|меня)\b", low):
            return True
        if re.search(r"\b(как ты выглядишь|покажи себя|покажись|хочу тебя увидеть|хочу увидеть тебя)\b", low):
            return True
        if re.search(r"\b(как ты|ты сама)\s+\w+", low):
            return True
        return False

    def _char_look(self, app) -> str:
        name = ""
        try:
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "")
            if not name:
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "") or "")
        except Exception:
            name = ""
        look = ""
        try:
            from character_catalog import read_character_card
            look = (read_character_card(name) or "")[:800]
        except Exception:
            look = ""
        return name or "персонаж", look or self._look_qwen()

    async def _llm_complete(self, app, system: str, user: str) -> str:
        """Один запрос к LLM. Возвращает текст ответа (content, затем reasoning_content).

        Сырой ответ (message) пишется в data/logs/imggen_prompt_raw.log
        для диагностики reasoning-моделей (DeepSeek V4 Flash и т.п.).
        """
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        llm = getattr(app, "llm", None)
        if llm is None:
            app.state["imggen_llm_error"] = "app.llm не создан"
            return ""
        try:
            msg: dict = {}
            if hasattr(llm, "chat_once_message"):
                msg = await llm.chat_once_message(
                    msgs, temperature=0.4, max_tokens=4096, raw_log="imggen"
                )
            elif hasattr(llm, "chat_once"):
                text = str(await llm.chat_once(msgs, temperature=0.4, max_tokens=4096) or "")
                msg = {"content": text}
            else:
                app.state["imggen_llm_error"] = "у LLM нет chat_once/chat_once_message"
                return ""
            self._log_llm_raw(app, msgs, msg)
            app.state["imggen_last_raw"] = str(msg.get("content") or msg.get("reasoning_content") or "")
            # ── диагностика: сырой JSON ────────────────────────────────────
            try:
                import json as _json
                print(
                    f"imggen llm raw json: {_json.dumps(msg, ensure_ascii=False)[:3000]}",
                    flush=True,
                )
            except Exception:
                pass
            print(
                f"imggen llm content: {msg.get('content')!r}",
                flush=True,
            )
            print(
                f"imggen llm reasoning: {msg.get('reasoning_content')!r}",
                flush=True,
            )
            print(
                f"imggen llm finish_reason: {msg.get('finish_reason')!s}",
                flush=True,
            )
            print(
                f"imggen llm usage: {msg.get('usage')}",
                flush=True,
            )
            # ── конец диагностики ───────────────────────────────────────────
            content = str(msg.get("content") or "").strip()
            if content:
                app.state["imggen_llm_error"] = ""
                return content
            reasoning = str(msg.get("reasoning_content") or "").strip()
            if reasoning:
                app.state["imggen_llm_error"] = ""
                return reasoning
            fr = str(msg.get("finish_reason") or "")
            ref = str(msg.get("refusal") or "")
            usage = msg.get("usage") or {}
            print(
                f"imggen llm: пустой ответ — finish_reason={fr!r} "
                f"refusal={ref[:120]!r} "
                f"usage.completion_tokens={usage.get('completion_tokens', '?')} "
                f"usage.total_tokens={usage.get('total_tokens', '?')}",
                flush=True,
            )
            app.state["imggen_llm_error"] = (
                f"пустой content/reasoning: finish_reason={fr} refusal={ref[:120]}"
            )
            return ""
        except Exception as e:
            print(f"imggen llm.chat_once: {e}", flush=True)
            app.state["imggen_llm_error"] = f"{type(e).__name__}: {e}"
            return ""

    def _llm_online_probe(self, llm) -> str:
        """Проверка доступности LLM в отдельном потоке (безопасно из async-контекста)."""
        if llm is None or not hasattr(llm, "ping"):
            return "неизвестно"
        result: dict = {}

        def probe():
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                try:
                    result["ok"] = bool(loop.run_until_complete(llm.ping()))
                finally:
                    loop.close()
            except Exception:
                result["ok"] = False

        import threading
        t = threading.Thread(target=probe, daemon=True)
        t.start()
        t.join(timeout=4)
        return "online" if result.get("ok") else "неизвестно"

    def _llm_fail_message(self, app, raw: str = "") -> str:
        """Диагностическое сообщение пользователю при сбое запроса промта к LLM."""
        url = "http://127.0.0.1:1234"
        model = "?"
        llm = getattr(app, "llm", None)
        try:
            if llm is not None:
                model = str(getattr(llm, "model", "?") or "?")
                client = getattr(llm, "_provider", None)
                client = getattr(client, "_client", None)
                if client is not None:
                    url = str(getattr(client, "api_url", "") or url)
        except Exception:
            pass
        reason = str(app.state.get("imggen_llm_error") or "LLM не вернула промт")
        online = self._llm_online_probe(llm)
        snippet = (raw or "").strip()[:300]
        return (
            "⚠ Не смогла получить промт от LLM.\n"
            f"Причина: {reason}\n"
            f"Сырой ответ (первые 300 символов): {snippet!r}\n"
            f"Проверь: {url} ({online}, модель: {model})"
        )

    def _llm_probe(self, app) -> None:
        """Диагностический запрос к LLM в фоновом потоке при старте."""
        llm = getattr(app, "llm", None)
        if llm is None or not hasattr(llm, "chat_once"):
            return
        probe_msgs = [
            {"role": "user", "content": "Ответь одной строкой: cat sitting on windowsill"},
        ]

        def _probe():
            import asyncio
            import json as _json
            try:
                loop = asyncio.new_event_loop()
                # Пробуем chat_once_message (возвращает dict с полными данными)
                if hasattr(llm, "chat_once_message"):
                    msg = loop.run_until_complete(
                        llm.chat_once_message(probe_msgs, temperature=0.1, max_tokens=50)
                    ) or {}
                    content = str(msg.get("content") or "").strip()
                    reasoning = str(msg.get("reasoning_content") or "").strip()
                    fr = str(msg.get("finish_reason") or "")
                    print(f"imggen llm probe raw: {_json.dumps(msg, ensure_ascii=False)[:3000]}", flush=True)
                    print(f"imggen llm probe content={content!r} reasoning={reasoning!r} finish_reason={fr!s}", flush=True)
                    text = content or reasoning
                else:
                    text = loop.run_until_complete(
                        llm.chat_once(probe_msgs, temperature=0.1, max_tokens=50)
                    )
                loop.close()
                text = (text or "").strip()[:200]
                print(f"imggen llm probe: ответ={text!r}", flush=True)
            except Exception as e:
                print(f"imggen llm probe: ошибка {type(e).__name__}: {e}", flush=True)

        import threading
        t = threading.Thread(target=_probe, daemon=True)
        t.start()
    def _log_llm_raw(self, app, msgs: list, msg: dict) -> None:
        """Аккуратный лог запроса/ответа imggen (без api_key) в imggen_prompt_raw.log."""
        try:
            log_dir = Path(__file__).resolve().parents[2] / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / "imggen_prompt_raw.log"
            entry = {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "stage": app.state.get("imggen_stage"),
                "messages": [
                    {"role": m.get("role"), "content": str(m.get("content"))[:150]}
                    for m in (msgs or [])
                ],
                "message": {
                    "content": str(msg.get("content"))[:800] if msg.get("content") else None,
                    "reasoning_content": str(msg.get("reasoning_content"))[:800]
                    if msg.get("reasoning_content")
                    else None,
                    "finish_reason": msg.get("finish_reason"),
                    "refusal": str(msg.get("refusal") or "")[:800],
                },
                "usage": msg.get("usage"),
            }
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            pass

    def _parse_prompt_json(self, raw: str) -> dict:
        s = (raw or "").strip()
        if "```" in s:
            s = s.split("```", 2)[1]
            if s.lower().startswith("json"):
                s = s[4:]
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j+1]
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return {k: str(v).strip() for k, v in data.items() if v}
        except Exception:
            pass
        return {}

    async def _llm_prompts_then_card(self, app, request: str, refs: list) -> str:
        name, look = self._char_look(app)
        about = self._about_character(request, name)
        if about:
            subject = (
                f"Персонаж «{name}» — девушка/female. На картинке должна быть девушка, не мужчина.\n"
                f"Запрос про персонажа чата «{name}». Внешность только из карточки:\n{look}\n"
                "Её и рисуй, если пользователь не назвал другого героя."
            )
        else:
            subject = (
                f"Запрос НЕ про персонажа чата («{name}»). "
                "НЕ вставляй её имя, уши, хвост, рыжие волосы и внешность. "
                "Рисуй только то, что в запросе пользователя."
            )
        system = (
            "Ты редактор промптов только для Qwen-Image-2.1. "
            "Верни ТОЛЬКО JSON без markdown:\n"
            '{"t2i":"...","edit":"..."}\n'
            "t2i — один английский абзац наблюдателя: The image is a ..., где что стоит, "
            "The lighting is..., The overall composition.... Без masterpiece и 8k.\n"
            "edit — английская инструкция правки с <image1>, что изменить и что оставить. "
            "Если референса нет, edit оставь пустым.\n"
            "Не вставляй персонажа чата, если запрос не про него."
        )
        user = (
            f"{subject}\n"
            f"Референс приложен: {'да' if refs else 'нет'}\n"
            f"Запрос пользователя: {request}\n"
            "Собери промпт t2i и, если есть референс, инструкцию edit."
        )
        raw = await self._llm_complete(app, system, user)
        data = self._parse_prompt_json(raw)
        t2i = extract_prompt_from_llm(raw)
        print(f"imggen llm prompts raw={raw[:180]!r} parsed={list(data.keys())}", flush=True)
        if not data.get("t2i") and not data.get("qwen"):
            if t2i:
                data["t2i"] = t2i
            else:
                return self._llm_fail_message(app, raw)
        t2i = data.get("t2i") or data.get("qwen") or ""
        edit = data.get("edit") or data.get("qwen_edit") or ""
        app.state["imggen_prompt_t2i"] = t2i
        app.state["imggen_prompt_edit"] = edit
        app.state["imggen_prompt"] = t2i
        app.state["imggen_last_prompt"] = t2i
        return self._ask_card_ready(app, refs)

    def _ask_card_ready(self, app, refs: list) -> str:
        t2i = str(app.state.get("imggen_prompt_t2i") or app.state.get("imggen_prompt") or "")
        edit = str(app.state.get("imggen_prompt_edit") or "")
        lines = [
            "Промпт для Qwen-Image 2.1:",
            f"Текст:\n«{t2i}»",
        ]
        if edit:
            lines.append(f"Правка:\n«{edit}»")
        lines.append("Если ок — «давай». Если нет — напиши, что изменить.")
        return "\n\n".join(lines)

    def _draft_prompt(self, app, raw: str, family: str = "") -> str:
        fam = self._family(family or "t2i")
        raw_scene = self._user_scene(raw) or "a clear subject in even light"
        scene = _translate_scene(raw_scene) if raw_scene != "a clear subject in even light" else raw_scene
        name, look = self._char_look(app)
        about = self._about_character(raw, name)
        if fam == "edit":
            keep = "Keep the pose, face, clothes, and composition from <image1>."
            if about:
                keep = f"Keep {name} as shown in <image1>: pose, face, and clothes, unless asked otherwise."
            return f"Edit <image1>. {keep} Change: {scene}."
        if about:
            return (
                f"The image is a vertical illustration of a cute anime girl with fox features — {name}. "
                f"She has long flowing red hair, fox ears, a fluffy tail, and bright blue eyes. "
                f"Scene: {scene}. The lighting is soft and even. "
                "The overall composition is a clear view of only this scene."
            )
        return (
            f"The image is an illustration of {scene}. "
            "The lighting is clear. The overall composition shows only what was requested."
        )

    def _user_scene(self, raw: str) -> str:

        extra = (raw or "").strip()
        for w in (
            "сгенерируй изображение", "сгенерируй картинку", "сгенерируй",
            "нарисуй мне", "нарисуй", "сделай картинку", "сделай изображение",
            "пришли фотку", "пришли фото", "скинь фотку", "скинь фото",
            "отредактируй", "поправь фото", "измени фото", "пожалуйста",
        ):
            extra = extra.replace(w, " ")
            extra = extra.replace(w.capitalize(), " ")
        return " ".join(extra.split())

    def _look_qwen(self) -> str:
        return (
            "аниме-девушка лиса: рыжие длинные волосы, лисьи уши, пушистый хвост, "
            "голубые глаза, узнаваемый персонаж Лисичка"
        )

    def _character_ref_img(self, app) -> str:
        """Сохранённое фото внешности: state, затем images/ref.*"""
        current = str(app.state.get("character_self_ref") or "")
        if current and Path(current).is_file():
            return current
        try:
            name = ""
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "")
            if not name:
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "") or "")
            if not name:
                return ""
            from character_catalog import character_dir
            img_dir = character_dir(name) / "images"
            if not img_dir.is_dir():
                return ""
            for stem in ("ref", "reference"):
                for ext in (".png", ".jpg", ".jpeg", ".webp"):
                    p = img_dir / f"{stem}{ext}"
                    if p.is_file():
                        app.state["character_self_ref"] = str(p)
                        return str(p)
        except Exception:
            pass
        return ""

    def _save_character_ref(self, app) -> str:
        """Сохранить прикреплённое/последнее изображение как ref.png персонажа."""
        try:
            name = ""
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "")
            if not name:
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "") or "")
            if not name:
                return "нет активного персонажа."
            from character_catalog import character_dir
            img_dir = character_dir(name) / "images"
            img_dir.mkdir(parents=True, exist_ok=True)
            refs = self._attached(app)
            if not refs:
                last = app.state.get("phone_media_last")
                if last and Path(str(last)).is_file():
                    refs = [str(last)]
            if not refs:
                # fallback: последняя сгенерированная картинка персонажа
                generated = Path(__file__).resolve().parents[2] / "generated" / name
                if generated.is_dir():
                    gens = sorted(generated.glob("gen_*.png")) + sorted(generated.glob("gen_*.jpg"))
                    if gens:
                        refs = [str(gens[-1])]
            if not refs:
                return "прикрепи фото или сначала сгенерируй себя."
            src = Path(refs[0])
            dest = img_dir / "ref.png"
            dest.write_bytes(src.read_bytes())
            from character_catalog import invalidate_card_cache
            invalidate_card_cache()
            app.state["character_self_ref"] = str(dest)
            print(f"imggen self ref saved: {dest}", flush=True)
            return f"запомнила. это я. когда рисую себя, беру это фото.\n[фото: {dest}]"
        except Exception as ex:
            return f"не смогла сохранить: {ex}"

    @staticmethod
    def _is_self_remember(text: str) -> bool:
        low = (text or "").lower().replace("ё", "е")
        low = re.sub(r"\[вложения:.*?\]", " ", low, flags=re.I | re.S)
        low = low.replace("этоты", "это ты").replace("это-ты", "это ты")
        low = " ".join(low.split())
        if any(k in low for k in (
            "запомни это", "запомни внешность", "запомни себя", "запомни образ",
            "это ты", "это я", "это твоя внешность", "это теперь ты", "это теперь я",
        )):
            return True
        return low.startswith("запомни") and any(k in low for k in (
            "внешность", "образ", "себя", "свой вид", "кадр", "картинк",
            "фото", "изображени",
        ))

    @staticmethod
    def _wants_show_ref(low: str) -> bool:
        text = (low or "").lower().replace("ё", "е")
        return any(k in text for k in (
            "как ты меня запомнила",
            "как ты меня помнишь",
            "покажи референс",
            "твой референс",
            "какое фото ты запомнила",
            "покажи как ты меня",
        ))

    def _show_character_ref(self, app, seeing: bool = False) -> str:
        path = self._character_ref_img(app)
        if not path:
            return "я ещё не запомнила своё фото. прикрепи картинку и скажи «запомни, это ты»."
        if seeing:
            return f"вот я.\n[фото: {path}]"
        return f"вот как я себя запомнила.\n[фото: {path}]"

    @staticmethod
    def _see_self_rest(low: str):
        """None = не про «увидеть её». Строка = хвост сцены после просьбы показаться."""
        text = " ".join((low or "").lower().replace("ё", "е").split())
        if any(w in text for w in ("найди", "поищи", "погугли", "загугли", "картинк", "в интернете")):
            if not any(w in text for w in ("себя", "тебя", "свое фото", "свой вид")):
                return None
        hit = ""
        for phrase in sorted(_SEE_SELF, key=len, reverse=True):
            if phrase in text:
                hit = phrase
                break
        if not hit:
            return None
        rest = text.replace(hit, " ", 1)
        rest = re.sub(
            r"\b(пожалуйста|плиз|мне|ну|там|тут|здесь|давай|можно|хочу|очень|просто|сейчас|уже|а|и|же|ты|себя|в чат|в чате)\b",
            " ",
            rest,
        )
        rest = re.sub(r"[^\w\s\-]", " ", rest, flags=re.U)
        return " ".join(rest.split())

    def _pending(self, app) -> List[str]:
        return self._image_files(list(app.state.get("pending_attachments") or []))

    def _recent_image(self, app) -> List[str]:
        last = str(app.state.get("phone_media_last") or "")
        if last and Path(last).is_file():
            return [last]
        older = self._image_files(list(app.state.get("last_attachments") or []))
        return older[:1]

    def _attached(self, app) -> List[str]:
        files = list(app.state.get("pending_attachments") or []) + list(app.state.get("last_attachments") or [])
        return self._image_files(files)

    def _refs(self, app) -> List[str]:
        files = list(app.state.get("pending_attachments") or []) + list(app.state.get("last_attachments") or [])
        last = app.state.get("phone_media_last")
        if last:
            files.append(last)
        return self._image_files(files)

    @staticmethod
    def _image_files(files) -> List[str]:
        out, seen = [], set()
        for f in files:
            p = Path(str(f))
            if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"} and p.exists() and str(p) not in seen:
                seen.add(str(p))
                out.append(str(p))
        return out

    def _base(self, app) -> str:
        raw = str(app.get_plugin_setting(self.id, "comfy_url", "http://127.0.0.1:8188") or "").rstrip("/")
        try:
            from urllib.parse import urlparse
            u = urlparse(raw)
            host = (u.hostname or "").lower()
            if u.scheme != "http" or host not in ("127.0.0.1", "localhost", "::1"):
                print(f"imggen: comfy_url не loopback ({raw!r})", flush=True)
                return ""
        except Exception:
            return ""
        return raw

    def _start(self, app, prompt, wf_path: Path, refs: List[str], job: float = 0):
        def work():
            try:
                msg = self.tool_generate(app, prompt=prompt, workflow=str(wf_path), refs=refs)
            except Exception as e:
                msg = f"генерация сломалась: {e}"
            if not job or app.state.get("imggen_job") == job:
                app.state["imggen_stage"] = "idle"
                app.state["imggen_done_at"] = time.time()
            path = app.state.get("phone_media_last")
            req = str(app.state.get("imggen_request") or "")
            ok = path and Path(str(path)).exists() and "ошиб" not in (msg or "").lower() and "не " not in (msg or "")[:18]
            if ok:
                msg = f"помнишь, ты просил «{req[:80]}»? вот кадр."
            self._notify(app, msg, path if ok else None)
        threading.Thread(target=work, name="imggen", daemon=True).start()

    def _notify(self, app, msg, path):
        def ui():
            gui = getattr(app, "window", None) or app.state.get("gui")
            text = msg or ""
            if path and Path(str(path)).exists():
                text = text + f"\n[фото: {path}]"
            if gui and hasattr(gui, "publish_assistant_message"):
                gui.publish_assistant_message(text)
                return
            if gui and hasattr(gui, "_append"):
                gui._append("Ассистент", text)
        win = getattr(app, "window", None) or app.state.get("gui")
        if win is not None and hasattr(win, "post"):
            win.post(ui)
            return
        try:
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import QThread
            appq = QApplication.instance()
            if appq is None or QThread.currentThread() is appq.thread():
                ui()
                return
        except Exception:
            pass
        ui()

    def _inbox(self) -> Path:
        return self._out_dir(self.app)

    def _out_dir(self, app) -> Path:
        name = "default"
        try:
            if hasattr(app, "get_active_character"):
                name = str(app.get_active_character() or "default")
            if not name:
                name = str(getattr(app.config, "ACTIVE_CHARACTER", "default") or "default")
        except Exception:
            pass
        # data/generated/<character>/
        root = Path(__file__).resolve().parents[2] / "generated" / name
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _is_stub_wf(self, path: Path) -> bool:
        try:
            raw = path.read_text(encoding="utf-8-sig")
            data = json.loads(raw)
        except Exception:
            return True
        if "prompt" in data and isinstance(data["prompt"], dict):
            data = data["prompt"]
        if not isinstance(data, dict) or not data:
            return True
        # настоящий API-граф: узлы с class_type
        nodes = 0
        for k, v in data.items():
            if str(k).startswith("_"):
                continue
            if isinstance(v, dict) and v.get("class_type"):
                nodes += 1
        return nodes < 2

    def _load_graph(self, path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if "prompt" in data and isinstance(data["prompt"], dict):
            data = data["prompt"]
        return copy.deepcopy(data)

    def _fill(self, app, graph: dict, prompt: str, refs: List[str]) -> dict:
        fixed = app.state.get("imggen_fixed_seed")
        seed = int(fixed) if fixed else random.randint(1, 2**31 - 1)
        app.state["imggen_last_seed"] = seed
        w = int(app.get_plugin_setting(self.id, "width", 1088) or 1088)
        h = int(app.get_plugin_setting(self.id, "height", 1808) or 1808)
        uploaded = None
        if refs:
            uploaded = self._upload(app, Path(refs[0]))
        for nid, node in graph.items():
            if not isinstance(node, dict):
                continue
            ct = node.get("class_type") or ""
            inp = node.setdefault("inputs", {})
            title = ((node.get("_meta") or {}).get("title") or "").lower()
            if ct == "PrimitiveStringMultiline" and self._is_prompt_slot(title):
                inp["value"] = prompt
            if ct in ("CLIPTextEncode", "TextEncodeQwenImageEdit", "TextEncodeQwenImageEditPlus"):
                field = "prompt" if "prompt" in inp else "text"
                cur = str(inp.get(field) or "")
                is_neg = "negative" in title
                is_pos = (not is_neg) and ("positive" in title or cur.strip() == "PROMPT")
                if is_pos and not isinstance(inp.get(field), list):
                    inp[field] = prompt
            if ct in ("EmptyLatentImage", "EmptySD3LatentImage"):
                if not isinstance(inp.get("width"), list):
                    inp["width"] = w
                if not isinstance(inp.get("height"), list):
                    inp["height"] = h
            if ct == "KSampler":
                inp["seed"] = seed
            if ct == "RandomNoise":
                inp["noise_seed"] = seed
            if ct == "LoadImage" and uploaded:
                inp["image"] = uploaded
        return graph

    @staticmethod
    def _is_prompt_slot(title: str) -> bool:
        if any(k in title for k in ("system", "rewrit", "эксперт", "pe ")):
            return False
        return any(k in title for k in ("prompt", "промпт", "изменить"))

    def _upload(self, app, path: Path) -> Optional[str]:
        try:
            import uuid
            boundary = uuid.uuid4().hex
            data = path.read_bytes()
            ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            body = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
            req = urllib.request.Request(
                self._base(app) + "/upload/image",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                method="POST",
            )
            resp = json.loads(urllib.request.urlopen(req, timeout=30).read().decode("utf-8"))
            name = resp.get("name") or path.name
            print(f"imggen: uploaded ref {name}", flush=True)
            return name
        except Exception as e:
            print(f"imggen: upload fail {e}", flush=True)
            return None

    def _post(self, url, payload, timeout=30):
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))

    def _get(self, url, timeout=30):
        return urllib.request.urlopen(url, timeout=timeout).read()

    def tool_generate(self, app, prompt="", workflow="", refs=None, source="", negative="", size="", **kw):
        prompt = (prompt or kw.get("text") or kw.get("query") or "").strip()
        source = str(source or kw.get("source") or "").strip().lower()
        refs = list(refs or [])
        edit = source in ("last", "uploaded", "generated") or "edit" in str(workflow or "").lower()
        wf = str(workflow or "").strip()
        run_now = bool(wf) and Path(wf).suffix.lower() == ".json" or (
            wf in _ALIASES or (wf and (self._resolve_wf(wf).exists()) and wf not in ("", "auto"))
        )
        # intent imggen / imggen_edit — карточка промптов, не сразу в очередь
        if not run_now:
            if not prompt:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_at"] = time.time()
                return "что рисуем? опиши сцену."
            scene = self._user_scene(prompt)
            if len(scene) < 8:
                app.state["imggen_stage"] = "await_prompt"
                app.state["imggen_request"] = prompt
                app.state["imggen_at"] = time.time()
                return "что рисуем? опиши сцену — промпт соберу один раз."
            if edit:
                app.state["imggen_workflow_hint"] = "правка"
            return self._after_scene(app, prompt)
        refs = refs or []
        base = self._base(app)
        if not base:
            return "ComfyUI только на localhost (127.0.0.1 / ::1). Проверь URL в настройках."
        path = Path(workflow) if workflow else self._resolve_wf(str(app.get_plugin_setting(self.id, "default_workflow", "qwen")))
        print(f"imggen: wf={path} prompt={prompt[:140]!r} refs={refs}", flush=True)
        try:
            graph = self._fill(app, self._load_graph(path), prompt, refs)
        except Exception as e:
            return f"не прочитан workflow: {e}"
        try:
            queued = self._post(base + "/prompt", {"prompt": graph})
        except Exception as e:
            return f"ComfyUI /prompt: {e}"
        print(f"imggen: queue {queued}", flush=True)
        if queued.get("error") or queued.get("node_errors"):
            return f"граф отклонён: {queued.get('error') or queued.get('node_errors')}"
        pid = str(queued.get("prompt_id") or "")
        if not pid:
            return f"нет prompt_id: {queued}"
        limit = int(app.get_plugin_setting(self.id, "timeout_sec", 900) or 900)
        t0 = time.time()
        hist = {}
        n = 0
        while time.time() - t0 < limit:
            time.sleep(1.5)
            n += 1
            try:
                hist = json.loads(self._get(self._base(app) + "/history/" + pid).decode("utf-8"))
            except Exception:
                continue
            if pid in hist:
                break
            if n % 4 == 0:
                print(f"imggen: wait {int(time.time()-t0)}s", flush=True)
        else:
            return f"не успела за {limit} сек."
        outputs = (hist.get(pid) or {}).get("outputs") or {}
        info = None
        for node in outputs.values():
            if node.get("images"):
                info = node["images"][0]
                break
        if not info:
            return "в history нет картинки."
        q = "?filename=%s&subfolder=%s&type=%s" % (
            quote(str(info.get("filename") or ""), safe=""),
            quote(str(info.get("subfolder") or ""), safe=""),
            quote(str(info.get("type") or "output"), safe=""),
        )
        blob = self._get(self._base(app) + "/view" + q, timeout=120)
        dest = self._out_dir(app) / f"gen_{int(time.time())}.png"
        dest.write_bytes(blob)
        app.state["phone_media_last"] = str(dest)
        self._record_generation(
            app,
            prompt=prompt,
            workflow=str(path),
            refs=refs,
            seed=app.state.get("imggen_last_seed"),
            dest=dest,
        )
        print(f"imggen: saved {dest}", flush=True)
        return f"[фото: {dest}]"


    def _record_generation(self, app, prompt="", workflow="", refs=None,
                           seed=None, dest=None, params=None) -> None:
        """Записать успешную генерацию в data/generations/history.jsonl."""
        try:
            if get_history is None:
                return
            h = self._history(app)
            if h is None:
                return
            rec = GenerationRecord(
                id=Path(dest).stem if dest else f"gen_{int(time.time())}",
                timestamp=time.time(),
                prompt=prompt or "",
                negative_prompt="",
                workflow=Path(str(workflow)).stem if workflow else str(app.state.get("imggen_last_wf") or "t2i"),
                refs=[str(r) for r in (refs or [])],
                output_path=str(dest) if dest else "",
                seed=int(seed) if seed else None,
                params=params or {
                    "width": app.state.get("imggen_width"),
                    "height": app.state.get("imggen_height"),
                    "steps": int(app.get_plugin_setting(self.id, "steps", 8) or 8),
                    "timeout_sec": int(app.get_plugin_setting(self.id, "timeout_sec", 900) or 900),
                },
                request=str(app.state.get("imggen_request") or ""),
                kind=str(app.state.get("imggen_kind") or ""),
            )
            h.add(rec)
            app.state["imggen_last_record"] = rec
        except Exception as e:
            print(f"imggen: history record fail: {e}", flush=True)


def register():
    return PluginImpl()







